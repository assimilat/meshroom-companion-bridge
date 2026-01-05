# This app is pre-alpha
Arocna3 Meshroom Assistant

A precision photogrammetry companion system designed to streamline the acquisition of high-quality image datasets for AliceVision Meshroom.

The system consists of a High-Precision Android Client (optimized for the Pixel 7 Pro) and a Workstation Bridge Server that provides real-time 3D telemetry visualization and project management.

🚀 The Core Problem

Photogrammetry often fails due to:

Motion Blur: Hand-held captures during movement.

Inconsistent Focus: Shallow depth of field causing "ghosting" in 3D meshes.

Coverage Gaps: Missing angles in the orbital path around an object.

Meshroom Assistant solves these by turning the smartphone into a spatial probe that only triggers the shutter when the device is steady, properly focused, and correctly positioned.

🏗 System Architecture

1. Android Client (/app)

Built with Kotlin and Jetpack Compose, utilizing CameraX with Camera2 Interop.

Sensor Fusion: Combines the Rotation Vector sensor suite to track Azimuth (0-360°) and Altitude (0-100 scale) in real-time.

Focal Telemetry: Hooks into the hardware capture session to extract real-time diopter values (lens focus distance).

Auto-Capture Engine: A two-stage calibration system (Discovery & Auto-Sweep) that ensures a perfect "depth stack" for the reconstruction.

2. Desktop Bridge Server (/bridge)

Built with Python 3.10+ and FastAPI.

Handshake Station: Generates unique QR codes (mbridge:// protocol) for instant LAN pairing.

Ingest Pipeline: Receives images and telemetry via multipart HTTP POST.

Live Monitor: A Three.js-powered 3D dashboard that renders a "Frustum Map" of every photo taken, allowing the user to see coverage gaps instantly.

Project Manager: Handles project creation, renaming, and persistent session recovery from disk.

🛠 Features

2-Stage Focal Calibration: - Discovery Phase: Maps the required focus range for a specific subject.

Sweep Phase: Automatically fires the shutter when the phone enters one of four calculated focal zones.

Stability Guard: Implements a 1200ms "Hold Steady" check before firing to ensure zero motion blur.

Real-time 3D Visualization: See your "camera path" rendered as green cones in a virtual space as you move.

EXIF Injection: Automatically tags images with heading, altitude, and focus distance in the UserComment metadata field.

Heartbeat Watchdog: Robust pairing logic that accurately tracks the connection status between the mobile device and workstation.

🚦 Getting Started

Prerequisites

Mobile: Android device (Physical device required for sensors; Pixel 7 Pro recommended).

Desktop: Python 3.10+, Wi-Fi connection on the same LAN as the phone.

Installation

Workstation Bridge:

cd bridge
pip install fastapi uvicorn qrcode pillow
python bridge_server.py


Open http://localhost:8080 in your browser.

Android App:

Import the root folder into Android Studio.

Build and deploy the app module to your device.

Workflow

Launch the Bridge Server on your PC.

Launch the Android App and tap PAIR.

Scan the QR code on your workstation monitor.

(Optional) Run Discovery to calibrate focus for your subject.

Begin your orbital sweep. The app will auto-capture when in range and steady.

Once finished, click View Files on the dashboard to find your Meshroom-ready dataset.

📝 Technical Notes

Communication: Uses a hybrid model of REST (for file transfers) and WebSockets (for live telemetry).

Metadata: Telemetry is injected into the Exif.Photo.UserComment field.

Hardware: Optimized for the Pixel 7 Pro lens suite (Ultra-Wide, Wide, Telephoto).

⚖️ License

Internal Project - Arocna3 Developments.
