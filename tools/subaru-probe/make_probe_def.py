#!/usr/bin/env python3
"""Build a RomRaider logger definition + logger profile for raw-RAM probing.

Why this exists: RomRaider only offers an ecuparam to a car whose <ecu id="..."> matches the ID the
ECU reports, and its parameter picker is slow to use for dozens of raw bytes.  This script
  1. takes your *stock* logger definition (e.g. logger_STD_EN_v370.xml),
  2. adds one SSM ecuparam per row of an address list (probe_params.csv) keyed to YOUR ECU ID,
  3. writes a profile that pre-selects those parameters (plus a few standard ones) so a single
     Profile > Load is all that is needed.

Usage (Windows, from this folder):
  python make_probe_def.py --base "C:\\Program Files\\RomRaider\\logger_STD_EN_v370.xml" ^
         --params probe_params.csv --ecu-id 8424387007,8424385107 --out-dir out
Then in RomRaider: Logger > Settings > Logger definition: pick out\\logger_PROBE.xml,
Logger > Profile > Load: out\\probe_profile.xml, then Start logging.

probe_params.csv columns: id,name,address,type[,units,expr]
  address  0xFFFF6990 or 0xFF6990 (the top byte is dropped; RomRaider SSM addresses are 24-bit)
  type     uint8|int8|uint16|int16|uint32|int32|float
  id       must start with X (X900...), unique, and not already present in the base file.
Lines starting with # are ignored.
"""
import argparse, csv, os, re, sys
from xml.sax.saxutils import quoteattr

LEN = {'uint8': 1, 'int8': 1, 'uint16': 2, 'int16': 2, 'uint32': 4, 'int32': 4, 'float': 4}
# standard parameters (by logger id: P8 Engine Speed, P9 Vehicle Speed, P13 Throttle Position,
# P12 Mass Airflow, P2 Coolant Temperature) and switches (matched by exact name) pre-selected in the profile
STD_PARAM_IDS = ['P8', 'P9', 'P13', 'P12', 'P2']
LEAN_X = ['X900', 'X901', 'X984', 'X985', 'X904', 'X905', 'X906', 'X908', 'X909', 'X943', 'X944']
LEAN_SWITCHES = ['Neutral Position Switch', 'Clutch Switch', 'Brake Switch', 'Stop Light Switch']
STD_SWITCHES = ['Neutral Position Switch', 'Clutch Switch', 'Brake Switch', 'Stop Light Switch', 'Idle Switch',
                'Cruise Control Main Toggle Switch', 'Cruise Control Set/Coast Switch',
                'Cruise Control Resume/Accelerate Switch', 'Cruise Control Cancel Switch',
                'Rear Defogger Switch', 'Light Switch', 'Handle Switch', 'Air Conditioning Switch',
                'Power Steering Switch', 'Traction Control System (TCS) Switch', 'ABS Signal']


def read_params(path):
    rows = []
    with open(path, newline='', encoding='utf-8-sig') as f:
        lines = [l for l in f if l.strip() and not l.lstrip().startswith('#')]
    for r in csv.DictReader(lines):
        pid = r['id'].strip()
        if not re.fullmatch(r'X\d+', pid):
            sys.exit('bad id %r: must be X<digits>' % pid)
        typ = r['type'].strip()
        if typ not in LEN:
            sys.exit('bad type %r for %s' % (typ, pid))
        addr = int(r['address'].strip(), 16) & 0xFFFFFF
        rows.append(dict(id=pid, name=r['name'].strip(), addr=addr, type=typ,
                         units=(r.get('units') or 'raw').strip() or 'raw',
                         expr=(r.get('expr') or 'x').strip() or 'x'))
    ids = [r['id'] for r in rows]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        sys.exit('duplicate ids: %s' % sorted(dup))
    return rows


def ecuparam_xml(r, ecu_id):
    fmt = '0.00' if r['type'] == 'float' else '0'
    name = quoteattr(r['name'])
    return ('<ecuparam id="%s" name=%s desc=%s target="1"><ecu id="%s"><address length="%d">0x%06X</address></ecu>'
            '<conversions><conversion units=%s storagetype="%s" expr=%s format="%s" /></conversions></ecuparam>'
            % (r['id'], name, name, ecu_id, LEN[r['type']], r['addr'], quoteattr(r['units']), r['type'],
               quoteattr(r['expr']), fmt))


def ssm_ecuparams_close(text):
    p = text.index('<protocol id="SSM"')
    e = text.index('<ecuparams>', p)
    c = text.index('</ecuparams>', e)
    if c > text.index('</protocol>', p):
        sys.exit('SSM protocol has no closing </ecuparams> before </protocol>; unexpected base file layout')
    return c


def std_param_ids(text):
    p = text.index('<protocol id="SSM"')
    body = text[p:text.index('</protocol>', p)]
    return [i for i in STD_PARAM_IDS if '<parameter id="%s" ' % i in body]


def std_ids(text, tag, names):
    p = text.index('<protocol id="SSM"')
    q = text.index('</protocol>', p)
    body = text[p:q]
    out = []
    for n in names:
        m = re.search(r'<%s id="([^"]+)" name=%s' % (tag, re.escape(quoteattr(n))), body)
        if m:
            out.append(m.group(1))
        else:
            print('note: standard %s %r not found in base file' % (tag, n), file=sys.stderr)
    return out


def write_profile(path, params, switches, comment=None):
    prof = ['<?xml version="1.0" encoding="UTF-8"?>'] + (['<!-- %s -->' % comment] if comment else []) + ['<profile protocol="SSM">', '  <parameters>']
    prof += ['    <parameter id="%s" livedata="selected" />' % i for i in params]
    prof += ['  </parameters>', '  <switches>']
    prof += ['    <switch id="%s" livedata="selected" />' % i for i in switches]
    prof += ['  </switches>', '</profile>', '']
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write('\n'.join(prof))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', required=True)
    ap.add_argument('--params', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'probe_params.csv'))
    ap.add_argument('--ecu-id', default='8424387007,8424385107',
                    help='one ECU id or a comma list; RomRaider offers a parameter if the connected ECU matches any')
    ap.add_argument('--out-dir', default='out')
    a = ap.parse_args()

    with open(a.base, encoding='utf-8-sig') as f:
        text = f.read()
    rows = read_params(a.params)
    # Skip ids the base already carries, so a previous probe definition can be extended in place.
    have = {r['id'] for r in rows if re.search(r'<ecuparam id="%s"' % r['id'], text)}
    if have:
        print('note: %d ids already in base file, skipped' % len(have), file=sys.stderr)
    new_rows = [r for r in rows if r['id'] not in have]
    for one in a.ecu_id.split(','):
        if one not in text:
            print('note: ECU id %s not mentioned anywhere in base file' % one, file=sys.stderr)

    block = ''.join(ecuparam_xml(r, a.ecu_id) for r in new_rows)
    c = ssm_ecuparams_close(text)
    out = text[:c] + block + text[c:]

    os.makedirs(a.out_dir, exist_ok=True)
    dpath = os.path.join(a.out_dir, 'logger_PROBE.xml')
    with open(dpath, 'w', encoding='utf-8', newline='') as f:
        f.write('\ufeff' + out)

    sp, sw = std_param_ids(text), std_ids(text, 'switch', STD_SWITCHES)
    write_profile(os.path.join(a.out_dir, 'probe_profile.xml'), sp[:3] + [r['id'] for r in rows] + sp[3:], sw)
    ppath = os.path.join(a.out_dir, 'probe_profile.xml')
    # lean profile: few channels => faster polling. X900 (RAM float RPM) is the positive control: it must track P8.
    lean = [i for i in LEAN_X if any(r['id'] == i for r in rows)]
    write_profile(os.path.join(a.out_dir, 'probe_profile_lean.xml'), ['P8', 'P9', 'P13'] + lean,
                  std_ids(text, 'switch', LEAN_SWITCHES))
    # batch G: the RAM bytes behind the native switch bytes (X1000+), next to the native switches they should mirror
    g = [r['id'] for r in rows if re.fullmatch(r'X10\d{2}', r['id'])]
    if g:
        write_profile(os.path.join(a.out_dir, 'probe_batch_G_native_switch_bytes.xml'), ['P8', 'P9', 'X900'] + g,
                      std_ids(text, 'switch', LEAN_SWITCHES + ['Cruise Control Set/Coast Switch',
                              'Cruise Control Resume/Accelerate Switch', 'Cruise Control Main Toggle Switch',
                              'Idle Switch', 'Handle Switch', 'Rear Defogger Switch', 'Light Switch', 'ABS Signal']),
                      'batch G: each X1000+ byte is the RAM cell behind one SSM switch byte (ROM table 0xA1A28); 0x121 byte X1019 bit7 should follow native clutch S63. ~%d addresses. K-line only. ECU ID shown: ____ | interval: ____ ms | log: ____' % (len(g) + 6))
    # batch GH: G plus H (table validation) in one stationary run; native switches and P2/P7/P8/P9/P13 give the ground truth
    # the u8 reads at table[0x0B,0x0E,0x0F,0x11,0x12,0x13] duplicate the typed reads X1114-X1119; leave them out of the profile
    dup = {'X1103', 'X1106', 'X1107', 'X1109', 'X1110', 'X1111'}
    h = [r['id'] for r in rows if re.fullmatch(r'X11\d{2}', r['id']) and r['id'] not in dup]
    if g and h:
        write_profile(os.path.join(a.out_dir, 'probe_batch_GH_table_check.xml'), ['P2', 'P7', 'P8', 'P9', 'P13', 'X900'] + g + h,
                      std_ids(text, 'switch', LEAN_SWITCHES + ['Cruise Control Set/Coast Switch',
                              'Cruise Control Resume/Accelerate Switch', 'Cruise Control Main Toggle Switch', 'Idle Switch']),
                      'batch GH: G (X1000+ switch-byte RAM) plus H (X1100+ table cells behind standard P2/P7/P8/P9/P13). ~%d RAM addresses + 5 standard + 8 switches; stays under the 68-address K-line limit. K-line, fastpoll=true. ECU ID shown: ____ | interval: ____ ms | log: ____' % (len(g) + len(h) + 4 + 6))
    # batch R: the RAM flags behind the native clutch/neutral/brake/cruise bits, next to those native bits
    r = [x['id'] for x in rows if re.fullmatch(r'X12\d{2}', x['id'])]
    if r:
        write_profile(os.path.join(a.out_dir, 'probe_batch_R_input_flags.xml'), ['P8', 'P9', 'X900'] + r,
                      std_ids(text, 'switch', LEAN_SWITCHES + ['Cruise Control Set/Coast Switch',
                              'Cruise Control Resume/Accelerate Switch', 'Cruise Control Main Toggle Switch',
                              'Cruise Control Cancel Switch', 'Idle Switch']),
                      'batch R: RAM flags behind the native SSM switch bits (clutch 692F, neutral 6908, brake 6238, stop 6239, cruise 6235-6237/623C/6249) plus port images and limiter/gate flags. ~%d RAM addresses; K-line, fastpoll=true; untick/retick P8/P9/switches after connect. ECU ID shown: ____ | interval: ____ ms | log: ____' % (len(r) + 5))
    print('wrote %s (%d probe params added, %d in list, ECU id %s)' % (dpath, len(new_rows), len(rows), a.ecu_id))
    print('wrote %s (%d standard params, %d switches pre-selected)' % (ppath, len(sp), len(sw)))


if __name__ == '__main__':
    main()
