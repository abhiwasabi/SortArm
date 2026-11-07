# XIAO ESP32-S3 camera setup

The dashboard reads individual JPEG snapshots from an ESP32 camera over local Wi-Fi. The board must be a **XIAO ESP32-S3 Sense**, or a XIAO ESP32-S3 with its compatible Sense camera expansion and camera ribbon attached. A plain XIAO ESP32-S3 board has no camera.

## Put the camera on Wi-Fi

1. Snap the included external Wi-Fi antenna onto the XIAO's small antenna connector, then connect the XIAO to this computer by USB. In Arduino IDE, open **File → Examples → ESP32 → Camera → CameraWebServer** and save a working copy **outside this repository**.
2. In that copy's `board_config.h`, comment out the default `CAMERA_MODEL_ESP_EYE` line and enable `CAMERA_MODEL_XIAO_ESP32S3`.
3. In `CameraWebServer.ino`, enter the Wi-Fi network name and password locally. Never commit that copy or paste the password into an issue or chat. The XIAO and laptop must be on the same network; the laptop also needs internet access for Gemini.
4. Select **XIAO_ESP32S3** as the board, **OPI PSRAM** under Tools → PSRAM, and a partition scheme with at least 3 MB app space. Select the XIAO USB port and upload.
5. Open Serial Monitor at **115200 baud**. After the board connects to Wi-Fi, it prints its IP address. Test `http://<board-ip>/capture` in a browser; it should return a single image. The separate `/stream` endpoint is MJPEG and is not the snapshot URL expected by the bridge.
6. Open the sorting dashboard without `?demo=1`. In **Settings → Camera source**, choose **ESP32-S3**, enter `http://<board-ip>/capture`, and apply. The Camera view should then show current frames. Changing sources pauses sorting.

If the preview is slow or repeatedly disconnects, close any browser tab running the board's **Start Stream** view before testing the dashboard. The board serves those stream frames separately from the dashboard's snapshot requests. Check that the external antenna is firmly attached and move the board closer to the 2.4 GHz Wi-Fi access point. Seeed notes that the antenna is needed for a reliable connection. The dashboard can only update as fast as the board returns each `/capture` image; test that URL directly if the preview remains slow.

If the board already has a working factory webcam program, you may use its JPEG snapshot URL instead of uploading the example. Verify that URL returns one JPEG image before entering it in Settings. The dashboard does not configure the ESP32's Wi-Fi or flash its firmware.

If Serial Monitor prints `camera: ok` followed by a Wi-Fi failure, the camera initialized correctly. Open the sketch currently on the board, correct its Wi-Fi name/password in `secrets.h` (or the example's `.ino`), use a 2.4 GHz network, and upload again. If that sketch is unavailable, use the CameraWebServer example above.

For Gemini classification, configure `GEMINI_API_KEY` and `GEMINI_MODEL` in the laptop's shell before starting the bridge. Keep those values out of the browser and Git. Camera preview can be checked before Gemini is configured.

Official references: [Seeed XIAO ESP32-S3 Sense camera guide](https://wiki.seeedstudio.com/xiao_esp32s3_camera_usage/#project-ii-video-streaming), [Seeed getting started guide](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/), and [Espressif CameraWebServer example](https://github.com/espressif/arduino-esp32/tree/master/libraries/ESP32/examples/Camera/CameraWebServer).
