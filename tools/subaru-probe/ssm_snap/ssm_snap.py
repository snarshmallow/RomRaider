#!/usr/bin/env python3
"""ssm_snap: read-only RAM snapshot + differential scan over Subaru SSM (K-line, J2534 / Tactrix OpenPort).

Purpose: find which RAM byte/bit follows a physical input (clutch pedal, brake, handbrake, toggle) without guessing
addresses. Take snapshots of a RAM span with the input in state A, state B, A, B, then list the bytes that are
identical in both A snapshots, identical in both B snapshots, and different between A and B.

Safety
- Read-only: the only ECU commands sent are the SSM init (0xBF) and SSM read (0xA8). No write, no flash, no kernel.
- Refuses addresses >= 0xFFC000 (on-chip I/O registers, some clear on read) unless --allow-io.
- Close RomRaider/SubaruEdit/EcuFlash first: only one program may hold the OpenPort.
- Needs 32-bit Python on Windows for the real adapter (op20pt32.dll is 32-bit). selftest/simulate run anywhere.

Frame format (from RomRaider traces, K-line SSM, 4800 baud, ISO9141 with NO_CHECKSUM flag):
  request  80 10 F0 L A8 sub addr3*n cksum   L = 2 + 3n, cksum = sum of preceding bytes & 0xFF
  response 80 F0 10 L E8 data*n cksum        L = 1 + n
  init     80 10 F0 01 BF 40  ->  80 F0 10 .. FF A2 ... (ECU id bytes 84 24 38 70 07 ...)
Addresses are 24-bit; 0xFF6990 means RAM 0xFFFF6990 (sign extension).

Commands
  selftest                          offline checks against frames recorded in RomRaider traces
  simulate                          end-to-end run against a simulated ECU (no hardware)
  probe  [--sub 01|00]              connect, init, read 4 known bytes (X900 RPM float @FF6990) and print them
  capture --start A --end B --states up,down,up,down --out DIR
  diff DIR                          list stable differences between states
"""
import argparse, ctypes, json, os, struct, sys, time

BLOCK = 64                 # addresses per A8 request (68 answered in the traces; stay under)
IO_START = 0xFFC000        # on-chip I/O begins above RAM; do not scan unless asked


# ---------------------------------------------------------------- frames
def tx_time_ms(nbytes, baud=4800):
    """Wire time of a message: 10 bits per byte (start + 8 data + stop)."""
    return nbytes * 10.0 * 1000.0 / baud


def cksum(b):
    return sum(b) & 0xFF


def frame(hdr_to, hdr_from, payload):
    body = bytes([0x80, hdr_to, hdr_from, len(payload)]) + bytes(payload)
    return body + bytes([cksum(body)])


def init_request():
    return frame(0x10, 0xF0, [0xBF])


def read_request(addrs, sub=0x01):
    p = bytearray([0xA8, sub])
    for a in addrs:
        p += bytes([(a >> 16) & 0xFF, (a >> 8) & 0xFF, a & 0xFF])
    return frame(0x10, 0xF0, p)


def parse_read_response(buf, n):
    """Return the n data bytes of an E8 response, or raise ValueError."""
    if len(buf) < n + 6:
        raise ValueError('short response %d < %d' % (len(buf), n + 6))
    buf = bytes(buf[:n + 6])
    if buf[0] != 0x80 or buf[1] != 0xF0 or buf[2] != 0x10 or buf[3] != n + 1 or buf[4] != 0xE8:
        raise ValueError('bad header %s' % buf[:5].hex())
    if buf[-1] != cksum(buf[:-1]):
        raise ValueError('bad checksum in %s' % buf.hex())
    return buf[5:-1]


# ---------------------------------------------------------------- transports
class FakeTransport:
    """Simulated ECU for offline tests. ram: dict addr->byte; hook(state) may change it between calls."""

    def __init__(self, ram_fn):
        self.ram_fn = ram_fn
        self.pending = b''

    def connect(self):
        pass

    def init(self):
        return bytes.fromhex('80F01069FFA210098424387007')

    def exchange(self, req, expect_len, timeout_ms=1500):
        n = (req[3] - 2) // 3
        addrs = [(req[6 + 3 * i] << 16) | (req[7 + 3 * i] << 8) | req[8 + 3 * i] for i in range(n)]
        data = bytes(self.ram_fn(a) for a in addrs)
        body = bytes([0x80, 0xF0, 0x10, n + 1, 0xE8]) + data
        return body + bytes([cksum(body)])

    def clear_line(self):
        pass

    def reinit(self):
        return self.init()

    def close(self):
        pass


class J2534Transport:
    """ctypes J2534 04.04 transport mirroring RomRaider's J2534ConnectionISO9141 (flags 0x200, loopback on)."""
    ISO9141, NO_CHECKSUM, PASS_FILTER = 3, 0x200, 1
    SET_CONFIG, CLEAR_RX = 0x02, 0x08
    P1_MAX, P3_MIN, P4_MIN, LOOPBACK, DATA_BITS, PARITY = 0x07, 0x0A, 0x0C, 0x03, 0x20, 0x16

    class MSG(ctypes.Structure):
        _fields_ = [('ProtocolID', ctypes.c_ulong), ('RxStatus', ctypes.c_ulong), ('TxFlags', ctypes.c_ulong),
                    ('Timestamp', ctypes.c_ulong), ('DataSize', ctypes.c_ulong), ('ExtraDataIndex', ctypes.c_ulong),
                    ('Data', ctypes.c_ubyte * 4128)]

    class SCONFIG(ctypes.Structure):
        _fields_ = [('Parameter', ctypes.c_ulong), ('Value', ctypes.c_ulong)]

    class SCONFIG_LIST(ctypes.Structure):
        _fields_ = [('NumOfParams', ctypes.c_ulong), ('ConfigPtr', ctypes.c_void_p)]

    def __init__(self, dll=r'C:\Windows\SysWOW64\op20pt32.dll', baud=4800):
        if struct.calcsize('P') != 4:
            sys.exit('J2534 adapter needs 32-bit Python (op20pt32.dll is 32-bit); this is %d-bit.' % (8 * struct.calcsize('P')))
        self.lib = ctypes.WinDLL(dll)
        self.baud = baud
        self.dev = ctypes.c_ulong()
        self.ch = ctypes.c_ulong()
        self.flt = ctypes.c_ulong()

    def _chk(self, rc, what):
        if rc != 0:
            raise RuntimeError('%s failed, J2534 status %d' % (what, rc))

    def _msg(self, data):
        m = self.MSG()
        m.ProtocolID, m.TxFlags, m.DataSize = self.ISO9141, 0, len(data)
        for i, v in enumerate(data):
            m.Data[i] = v
        return m

    def connect(self):
        L = self.lib
        self._chk(L.PassThruOpen(None, ctypes.byref(self.dev)), 'PassThruOpen')
        self._chk(L.PassThruConnect(self.dev, self.ISO9141, self.NO_CHECKSUM, self.baud, ctypes.byref(self.ch)),
                  'PassThruConnect')
        items = (self.SCONFIG * 6)(
            self.SCONFIG(self.P1_MAX, 1), self.SCONFIG(self.P3_MIN, 1), self.SCONFIG(self.P4_MIN, 0),
            self.SCONFIG(self.LOOPBACK, 1), self.SCONFIG(self.DATA_BITS, 0), self.SCONFIG(self.PARITY, 0))
        lst = self.SCONFIG_LIST(6, ctypes.cast(items, ctypes.c_void_p).value)
        self._chk(L.PassThruIoctl(self.ch, self.SET_CONFIG, ctypes.byref(lst), None), 'SET_CONFIG')
        mask, pat = self._msg(b'\x00'), self._msg(b'\x00')
        self._chk(L.PassThruStartMsgFilter(self.ch, self.PASS_FILTER, ctypes.byref(mask), ctypes.byref(pat), None,
                                           ctypes.byref(self.flt)), 'StartMsgFilter')

    def _write(self, data, timeout_ms=None):
        if timeout_ms is None:                # J2534 WriteMsgs blocks until the bytes are sent: allow wire time + margin
            timeout_ms = int(tx_time_ms(len(data), self.baud)) + 1500
        m = self._msg(data)
        n = ctypes.c_ulong(1)
        self._chk(self.lib.PassThruWriteMsgs(self.ch, ctypes.byref(m), ctypes.byref(n), timeout_ms), 'WriteMsgs')

    def _read_for(self, want, timeout_ms):
        """Collect response bytes (skipping TX loopback and empty start-of-message indications)."""
        out = bytearray()
        end = time.time() + timeout_ms / 1000.0
        while time.time() < end and (want is None or len(out) < want):
            m = self.MSG()
            n = ctypes.c_ulong(1)
            rc = self.lib.PassThruReadMsgs(self.ch, ctypes.byref(m), ctypes.byref(n), 50)
            if rc != 0 or n.value == 0:
                continue
            if m.RxStatus & 0x03 or m.DataSize == 0:      # TX_MSG_TYPE or START_OF_MESSAGE
                continue
            out += bytes(m.Data[:m.DataSize])
        return bytes(out)

    def init(self):
        self._write(init_request())
        return self._read_for(None, 600)

    def exchange(self, req, expect_len, timeout_ms=1500):
        self._write(req)
        return self._read_for(expect_len, timeout_ms)

    def clear_line(self):
        self._write(bytes(20))              # RomRaider's "line break": 20 zero bytes, then drain until quiet
        while self._read_for(None, 100):
            pass

    def reinit(self):
        """Line break, drain, SSM init; return the init response (caller checks the ECU id)."""
        self.clear_line()
        time.sleep(0.3)
        return self.init()

    def close(self):
        L = self.lib
        for fn, a in ((L.PassThruStopMsgFilter, (self.ch, self.flt)), (L.PassThruDisconnect, (self.ch,)),
                      (L.PassThruClose, (self.dev,))):
            try:
                fn(*a)
            except Exception:
                pass


# ---------------------------------------------------------------- snapshot / diff
def read_block(tr, start, n, sub, retries=3):
    addrs = list(range(start, start + n))
    req = read_request(addrs, sub)
    for attempt in range(retries):
        try:
            resp = tr.exchange(req, n + 6)
            data = parse_read_response(resp, n)
            if sub == 0x01:
                tr.clear_line()             # sub 01 streams; stop it before the next request, as RomRaider does
            return data
        except (ValueError, RuntimeError) as e:
            last = e
            try:
                tr.reinit()
            except Exception as e2:            # keep trying; the final error reports both
                last = '%s; reinit also failed: %s' % (e, e2)
    raise RuntimeError('block %06X failed after %d tries: %s' % (start, retries, last))


ECU_IDS = (bytes.fromhex('8424387007'), bytes.fromhex('8424385107'))


def fresh_session(tr):
    init = tr.reinit()
    if not any(i in init for i in ECU_IDS):
        raise RuntimeError('ECU id missing from init response (%s); K-line session not usable' % init[:12].hex().upper())


def snapshot(tr, start, end, sub, progress=None):
    fresh_session(tr)
    out = bytearray()
    a = start
    while a < end:
        n = min(BLOCK, end - a)
        out += read_block(tr, a, n, sub)
        a += n
        if progress:
            progress(a - start, end - start)
    return bytes(out)


def stable_diff(groups):
    """groups: {state: [bytes,...]}. Returns list of (offset, {state: value}) where every state is internally
    constant across its snapshots and the states differ."""
    states = list(groups)
    size = len(groups[states[0]][0])
    hits = []
    for i in range(size):
        vals = {}
        ok = True
        for s in states:
            v = {snap[i] for snap in groups[s]}
            if len(v) != 1:
                ok = False
                break
            vals[s] = next(iter(v))
        if ok and len(set(vals.values())) > 1:
            hits.append((i, vals))
    return hits


def baseline_diff(groups, base='up'):
    """Per-state comparison against a baseline state. Returns rows (offset, base_value, {state: value|None}) for bytes
    whose baseline is constant and where at least one other state is constant and different. None = unstable in that state."""
    size = len(groups[base][0])
    others = [s for s in groups if s != base]
    rows = []
    for i in range(size):
        bv = {snap[i] for snap in groups[base]}
        if len(bv) != 1:
            continue
        b = next(iter(bv))
        vals, any_diff = {}, False
        for s in others:
            v = {snap[i] for snap in groups[s]}
            if len(v) != 1:
                vals[s] = None
            else:
                x = next(iter(v))
                vals[s] = x
                any_diff |= x != b
        if any_diff:
            rows.append((i, b, vals))
    return rows


def cmd_diff(d, base='up'):
    meta = json.load(open(os.path.join(d, 'meta.json')))
    groups = {}
    for fn in sorted(os.listdir(d)):
        if fn.startswith('snap_') and fn.endswith('.bin'):
            state = fn.split('_')[2].split('.')[0]
            groups.setdefault(state, []).append(open(os.path.join(d, fn), 'rb').read())
    if base not in groups:
        sys.exit('baseline state "%s" not among %s' % (base, list(groups)))
    rows = baseline_diff(groups, base)
    print('span %06X-%06X, states %s, snapshots %s, baseline "%s"' % (
        meta['start'], meta['end'], list(groups), {s: len(v) for s, v in groups.items()}, base))
    print('%d bytes: baseline constant, at least one other state constant and different ("=" same as baseline, "~" unstable):' % len(rows))
    for off, b, vals in rows:
        cells = []
        for s, v in vals.items():
            cells.append('%s=%s' % (s, '~' if v is None else ('=' if v == b else '%02X' % v)))
        changed = [s for s, v in vals.items() if v is not None and v != b]
        tag = ''
        if len(changed) == 1 and all(v is None or v == b for s, v in vals.items() if s != changed[0]):
            x = b ^ vals[changed[0]]
            tag = '  <- only "%s", xor=%02X%s' % (changed[0], x, ' (single bit)' if bin(x).count('1') == 1 else '')
        print('  %06X  %s=%02X  %s%s' % (meta['start'] + off, base, b, '  '.join(cells), tag))
    return rows


def cmd_capture(tr, a, sub):
    os.makedirs(a.out, exist_ok=True)
    meta = dict(start=a.start, end=a.end, states=a.states, sub=sub, block=BLOCK, taken=time.strftime('%F %T'))
    json.dump(meta, open(os.path.join(a.out, 'meta.json'), 'w'))
    blocks = -(-(a.end - a.start) // BLOCK)
    print('span %06X-%06X = %d bytes, %d requests per pass, roughly %.0f s per pass at 4800 baud' %
          (a.start, a.end, a.end - a.start, blocks, blocks * 0.8))
    counts = {}
    for idx, st in enumerate(a.states):
        if not a.no_prompt:
            input('\nState "%s" (%d/%d): set it now and keep it held for the whole pass, then press Enter... ' %
                  (st, idx + 1, len(a.states)))
        for s in range(a.countdown, 0, -1):
            print('\r  hold "%s"; pass starts in %d s ' % (st, s), end='', flush=True)
            print('\a', end='', flush=True)
            time.sleep(1)
        print('\r  PASS "%s" RUNNING - keep it held until the end beep      ' % st)
        t0 = time.time()
        data = snapshot(tr, a.start, a.end, sub,
                        progress=lambda d, t: print('\r  %5.1f%%' % (100.0 * d / t), end='', flush=True))
        counts[st] = counts.get(st, 0) + 1
        print('\a\a', end='', flush=True)
        fn = os.path.join(a.out, 'snap_%02d_%s.bin' % (idx, st))
        open(fn, 'wb').write(data)
        print('\r  state %s: %d bytes in %.0f s -> %s' % (st, len(data), time.time() - t0, fn))
    print('\nNow run:  ssm_snap.py diff "%s"' % a.out)


# ---------------------------------------------------------------- tests / simulation
RECORDED_REQ = ('8010F050A801FF9BAC000121FF9BA3FF9B80FF9AD0FF9ACCFF9B76FF9BABFF9B9CFF9B7CFF9B7EFF9ADCFF9B7A'
                'FF9D96FF9AD8FF9D5AFF9B98FF9BAAFF9AD4FF6990FF6991FF6992FF6993FF9B78FF9B9A0000D7C7')
RECORDED_RESP = '80F0101BE800000000003F0001000000000000000000003F447DF1D100000085'


def cmd_selftest():
    ok = True

    def check(name, cond):
        nonlocal ok
        print(('PASS ' if cond else 'FAIL ') + name)
        ok &= bool(cond)

    check('init request == recorded 8010F001BF40', init_request().hex().upper() == '8010F001BF40')
    addrs = [0xFF9BAC, 0x000121, 0xFF9BA3, 0xFF9B80, 0xFF9AD0, 0xFF9ACC, 0xFF9B76, 0xFF9BAB, 0xFF9B9C, 0xFF9B7C,
             0xFF9B7E, 0xFF9ADC, 0xFF9B7A, 0xFF9D96, 0xFF9AD8, 0xFF9D5A, 0xFF9B98, 0xFF9BAA, 0xFF9AD4, 0xFF6990,
             0xFF6991, 0xFF6992, 0xFF6993, 0xFF9B78, 0xFF9B9A, 0x0000D7]
    check('26-address read request == recorded RomRaider request', read_request(addrs, 1).hex().upper() == RECORDED_REQ)
    data = parse_read_response(bytes.fromhex(RECORDED_RESP), 26)
    check('recorded response parses (26 data bytes, checksum ok)', len(data) == 26 and data[-1] == 0)
    rpm = struct.unpack('>f', data[19:23])[0]
    check('RAM RPM float in the recorded response decodes (%.1f rpm)' % rpm, 900 < rpm < 1100)
    bad = bytearray(bytes.fromhex(RECORDED_RESP))
    bad[10] ^= 1
    try:
        parse_read_response(bytes(bad), 26)
        check('corrupted response rejected', False)
    except ValueError:
        check('corrupted response rejected', True)
    check('IO guard constant', IO_START == 0xFFC000)
    need = tx_time_ms(len(read_request(range(0xFF9800, 0xFF9840))))
    check('64-address request needs %.0f ms on the wire (the old 100 ms write timeout could not work)' % need, 400 < need < 520)
    check('init request wire time is small (%.0f ms)' % tx_time_ms(6), tx_time_ms(6) < 20)
    print('SELFTEST ' + ('OK' if ok else 'FAILED'))
    return 0 if ok else 1


def cmd_simulate():
    # simulated RAM: counters drift (noise); FF9D00 bit7 follows the clutch; FF9F10 bits 6/3 follow the brake
    state = {'s': 'up', 'tick': 0}

    def ram(a):
        state['tick'] += 1
        if a == 0xFF9D00:
            return 0x80 if state['s'] in ('clutch', 'both') else 0x00
        if a == 0xFF9F10:
            return 0x10 | (0x48 if state['s'] in ('brake', 'both') else 0)
        if 0xFF9E00 <= a < 0xFF9E08:                 # free-running counters
            return state['tick'] & 0xFF
        return (a * 7) & 0xFF

    tr = FakeTransport(ram)
    groups = {}
    for st in ('up', 'clutch', 'up', 'clutch', 'up', 'brake', 'up', 'brake', 'both'):
        state['s'] = st
        groups.setdefault(st, []).append(snapshot(tr, 0xFF9C00, 0xFFA000, 0x01))
    rows = baseline_diff(groups, 'up')
    found = {0xFF9C00 + o: (b, v) for o, b, v in rows}
    print('simulated baseline diff:', {('%06X' % a): v for a, v in sorted(found.items())})
    good = (set(found) == {0xFF9D00, 0xFF9F10}
            and found[0xFF9D00][1]['clutch'] == 0x80 and found[0xFF9D00][1]['brake'] == 0x00
            and found[0xFF9F10][1]['brake'] == 0x58 and found[0xFF9F10][1]['clutch'] == 0x10)
    print('SIMULATE ' + ('OK' if good else 'FAILED'))
    return 0 if good else 1


def parse_addr(s):
    return int(s, 16) if s.lower().startswith('0x') or any(c in s.lower() for c in 'abcdef') else int(s)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    sp.add_parser('selftest')
    sp.add_parser('simulate')
    p = sp.add_parser('probe')
    p.add_argument('--sub', default='01')
    p.add_argument('--n', type=int, default=4, help='bytes to read from FF6990 (use 64 to test a full-size request)')
    p.add_argument('--dll', default=r'C:\Windows\SysWOW64\op20pt32.dll')
    c = sp.add_parser('capture')
    c.add_argument('--start', type=parse_addr, required=True)
    c.add_argument('--end', type=parse_addr, required=True)
    c.add_argument('--states', default='up,down,up,down')
    c.add_argument('--out', required=True)
    c.add_argument('--sub', default='01')
    c.add_argument('--dll', default=r'C:\Windows\SysWOW64\op20pt32.dll')
    c.add_argument('--allow-io', action='store_true')
    c.add_argument('--no-prompt', action='store_true')
    c.add_argument('--countdown', type=int, default=5, help='seconds between setting the state and starting the pass')
    d = sp.add_parser('diff')
    d.add_argument('dir')
    d.add_argument('--base', default='up', help='baseline state name (default up)')
    a = ap.parse_args()

    if a.cmd == 'selftest':
        return cmd_selftest()
    if a.cmd == 'simulate':
        return cmd_simulate()
    if a.cmd == 'diff':
        cmd_diff(a.dir, a.base)
        return 0
    sub = int(a.sub, 16)
    tr = J2534Transport(a.dll)
    try:
        tr.connect()
        init = tr.init()
        print('init response (%d bytes): %s' % (len(init), init[:24].hex().upper()))
        if bytes.fromhex('8424387007') not in init and bytes.fromhex('8424385107') not in init:
            sys.exit('ECU id not found in init response; not continuing.')
        if a.cmd == 'probe':
            data = read_block(tr, 0xFF6990, a.n, sub)
            print('FF6990..%02X = %s' % (0x90 + a.n - 1, data.hex().upper()))
            print('RAM RPM float @FF6990 = %.1f (compare with the tach, ~1000 at idle); %d-byte request OK' %
                  (struct.unpack('>f', data[:4])[0], a.n))
            return 0
        a.states = a.states.split(',')
        if a.end > IO_START and not a.allow_io:
            sys.exit('refusing to scan %06X: addresses >= %06X are on-chip I/O (use --allow-io only if you know why)' %
                     (a.end, IO_START))
        if a.start < 0xFF0000:
            sys.exit('span must be inside RAM (0xFF0000-0xFFFFFF)')
        cmd_capture(tr, a, sub)
    finally:
        tr.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
