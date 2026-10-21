/*
 * RomRaider Open-Source Tuning, Logging and Reflashing
 * Copyright (C) 2006-2026 RomRaider.com
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 */

package com.romraider.logger.ecu;

import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

import javax.swing.JOptionPane;
import javax.swing.SwingUtilities;
import javax.xml.parsers.DocumentBuilderFactory;

import org.apache.log4j.Logger;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;

import com.romraider.util.SettingsManager;

/**
 * Runs a bounded, declarative capture plan through the existing RomRaider logger.
 * The plan cannot execute Java, Python, or shell code and does not expose ECU write APIs.
 */
final class CaptureScriptRunner implements Runnable {
    private static final Logger LOGGER = Logger.getLogger(CaptureScriptRunner.class);
    private static final long IDENTITY_POLL_MILLIS = 250;
    private final EcuLogger logger;
    private final File scriptFile;

    CaptureScriptRunner(EcuLogger logger, File scriptFile) {
        this.logger = logger;
        this.scriptFile = scriptFile;
    }

    @Override
    public void run() {
        CapturePlan plan = null;
        FileWriter markers = null;
        boolean startedLogger = false;
        boolean startedCapture = false;
        long captureStart = 0;
        try {
            plan = loadPlan(scriptFile);
            if (logger.isFileCaptureActive()) {
                throw new IOException("Stop the current manual file capture before running a script.");
            }

            if (!logger.isLogging()) {
                logger.startLogging();
                startedLogger = true;
            }
            waitForExpectedEcu(plan);
            runOnEdt(new Runnable() {
                @Override
                public void run() {
                    // Flush the EDT queue first so the ECU-specific logger rows are loaded.
                }
            });
            logger.selectCaptureChannels(plan.channelIds);

            File outputDirectory = new File(SettingsManager.getSettings().getLoggerOutputDirPath());
            if (!outputDirectory.isDirectory()) {
                throw new IOException("RomRaider's configured log folder does not exist: " + outputDirectory);
            }
            String stamp = new SimpleDateFormat("yyyyMMdd_HHmmss").format(new Date());
            String safeName = plan.name.replaceAll("[^A-Za-z0-9._-]", "_");
            File markerFile = new File(outputDirectory,
                    "romraider_events_" + safeName + "_" + stamp + ".csv");
            markers = new FileWriter(markerFile);
            markers.write("timestamp_epoch_ms,elapsed_ms,event,step_id,note"
                    + System.getProperty("line.separator"));
            markers.flush();

            logger.startScriptedFileCapture(plan.name);
            startedCapture = true;
            captureStart = System.currentTimeMillis();
            writeMarker(markers, captureStart, captureStart, "CAPTURE_START", "", plan.name);

            for (Step step : plan.steps) {
                int answer = showStepPrompt(step);
                if (answer != JOptionPane.OK_OPTION) {
                    writeMarker(markers, captureStart, System.currentTimeMillis(),
                            "CANCELLED", step.id, "Operator cancelled the capture.");
                    break;
                }

                long stepStart = System.currentTimeMillis();
                writeMarker(markers, captureStart, stepStart, "STEP_START", step.id, step.prompt);
                sleep(step.seconds * 1000L);
                writeMarker(markers, captureStart, System.currentTimeMillis(), "STEP_END", step.id, "");
            }
            writeMarker(markers, captureStart, System.currentTimeMillis(), "CAPTURE_END", "", "");
            showMessage("Scripted capture finished. Marker file: " + markerFile,
                    "RomRaider scripted capture", JOptionPane.INFORMATION_MESSAGE);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            reportFailure("Capture script was interrupted.", e);
        } catch (Exception e) {
            reportFailure(e.getMessage(), e);
        } finally {
            if (startedCapture) {
                try {
                    logger.stopScriptedFileCapture();
                } catch (Exception e) {
                    LOGGER.error("Unable to stop scripted file capture.", e);
                }
            }
            if (markers != null) {
                try {
                    markers.close();
                } catch (IOException e) {
                    LOGGER.error("Unable to close scripted capture marker file.", e);
                }
            }
            if (startedLogger) {
                try {
                    logger.stopLogging();
                } catch (Exception e) {
                    LOGGER.error("Unable to stop logger after scripted capture.", e);
                }
            }
        }
    }

    private void waitForExpectedEcu(CapturePlan plan) throws Exception {
        if (!logger.isLogging()) {
            throw new IOException("RomRaider did not start the logger. Select a serial port and connect first.");
        }
        long deadline = System.currentTimeMillis() + plan.connectionTimeoutSeconds * 1000L;
        while (System.currentTimeMillis() < deadline) {
            String ecuId = logger.getCurrentEcuId();
            if (ecuId != null) {
                if (!plan.expectedEcuId.equals(ecuId)) {
                    throw new IOException("Connected ECU ID " + ecuId
                            + " does not match this script's required ID " + plan.expectedEcuId + ".");
                }
                return;
            }
            Thread.sleep(IDENTITY_POLL_MILLIS);
        }
        throw new IOException("Timed out waiting for ECU identification. No capture was started.");
    }

    private CapturePlan loadPlan(File file) throws Exception {
        if (file == null || !file.isFile()) {
            throw new IOException("Capture script file not found: " + file);
        }
        DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
        factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
        factory.setFeature("http://xml.org/sax/features/external-general-entities", false);
        factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
        factory.setExpandEntityReferences(false);
        Document document = factory.newDocumentBuilder().parse(file);
        Element root = document.getDocumentElement();
        if (root == null || !"capture-script".equals(root.getTagName())) {
            throw new IOException("Root element must be <capture-script>.");
        }

        String name = requiredAttribute(root, "name");
        String expectedEcuId = requiredAttribute(root, "expected-ecu-id");
        int timeout = integerAttribute(root, "connection-timeout-seconds", 120);
        if (timeout < 10 || timeout > 900) {
            throw new IOException("connection-timeout-seconds must be between 10 and 900.");
        }

        Set<String> channelIds = new LinkedHashSet<String>();
        NodeList channels = root.getElementsByTagName("channel");
        for (int i = 0; i < channels.getLength(); i++) {
            Node node = channels.item(i);
            if (node instanceof Element) {
                channelIds.add(requiredAttribute((Element) node, "id"));
            }
        }
        if (channelIds.isEmpty()) {
            throw new IOException("The script must list at least one <channel id=\"...\"/>.");
        }

        List<Step> steps = new ArrayList<Step>();
        NodeList stepNodes = root.getElementsByTagName("step");
        for (int i = 0; i < stepNodes.getLength(); i++) {
            Node node = stepNodes.item(i);
            if (node instanceof Element) {
                Element element = (Element) node;
                String id = requiredAttribute(element, "id");
                String prompt = requiredAttribute(element, "prompt");
                int seconds = integerAttribute(element, "seconds", 0);
                if (seconds < 1 || seconds > 3600) {
                    throw new IOException("Each step duration must be between 1 and 3600 seconds.");
                }
                steps.add(new Step(id, prompt, seconds));
            }
        }
        if (steps.isEmpty()) {
            throw new IOException("The script must contain at least one <step/>.");
        }
        return new CapturePlan(name, expectedEcuId, timeout, channelIds, steps);
    }

    private String requiredAttribute(Element element, String name) throws IOException {
        String value = element.getAttribute(name);
        if (value == null || value.trim().length() == 0) {
            throw new IOException("Missing required attribute '" + name + "' on <"
                    + element.getTagName() + ">.");
        }
        return value.trim();
    }

    private int integerAttribute(Element element, String name, int defaultValue) throws IOException {
        String value = element.getAttribute(name);
        if (value == null || value.length() == 0) {
            return defaultValue;
        }
        try {
            return Integer.parseInt(value);
        } catch (NumberFormatException e) {
            throw new IOException("Attribute '" + name + "' must be an integer.");
        }
    }

    private int showStepPrompt(final Step step) throws Exception {
        final int[] answer = new int[] { JOptionPane.CANCEL_OPTION };
        runOnEdt(new Runnable() {
            @Override
            public void run() {
                answer[0] = JOptionPane.showConfirmDialog(logger,
                        step.prompt + "\n\nPress OK when the action is stable. The logger will mark and capture "
                                + step.seconds + " seconds.",
                        "Capture step: " + step.id,
                        JOptionPane.OK_CANCEL_OPTION,
                        JOptionPane.INFORMATION_MESSAGE);
            }
        });
        return answer[0];
    }

    private void showMessage(final String message, final String title, final int type) throws Exception {
        runOnEdt(new Runnable() {
            @Override
            public void run() {
                JOptionPane.showMessageDialog(logger, message, title, type);
            }
        });
    }

    private void reportFailure(String message, Exception cause) {
        LOGGER.error(message, cause);
        String text = message == null ? cause.toString() : message;
        try {
            showMessage(text, "RomRaider scripted capture", JOptionPane.ERROR_MESSAGE);
        } catch (Exception e) {
            LOGGER.error("Unable to display capture-script error.", e);
        }
    }

    private void runOnEdt(Runnable runnable) throws Exception {
        if (SwingUtilities.isEventDispatchThread()) {
            runnable.run();
        } else {
            SwingUtilities.invokeAndWait(runnable);
        }
    }

    private void sleep(long millis) throws InterruptedException {
        Thread.sleep(millis);
    }

    private void writeMarker(FileWriter writer, long captureStart, long timestamp,
            String event, String stepId, String note) throws IOException {
        writer.write(String.valueOf(timestamp));
        writer.write(",");
        writer.write(String.valueOf(timestamp - captureStart));
        writer.write(",");
        writer.write(csv(event));
        writer.write(",");
        writer.write(csv(stepId));
        writer.write(",");
        writer.write(csv(note));
        writer.write(System.getProperty("line.separator"));
        writer.flush();
    }

    private String csv(String value) {
        String text = value == null ? "" : value;
        return "\"" + text.replace("\"", "\"\"") + "\"";
    }

    private static final class CapturePlan {
        private final String name;
        private final String expectedEcuId;
        private final int connectionTimeoutSeconds;
        private final Set<String> channelIds;
        private final List<Step> steps;

        private CapturePlan(String name, String expectedEcuId, int connectionTimeoutSeconds,
                Set<String> channelIds, List<Step> steps) {
            this.name = name;
            this.expectedEcuId = expectedEcuId;
            this.connectionTimeoutSeconds = connectionTimeoutSeconds;
            this.channelIds = channelIds;
            this.steps = steps;
        }
    }

    private static final class Step {
        private final String id;
        private final String prompt;
        private final int seconds;

        private Step(String id, String prompt, int seconds) {
            this.id = id;
            this.prompt = prompt;
            this.seconds = seconds;
        }
    }
}
