## Pepe Translator
Pepe Translator is a macOS test application built to experiment with OpenAI Realtime API, providing:

- 🎤 Live microphone-based speech transcription (Whisper)
- 🔄 Instant Japanese translation (GPT)
- 🖥 Simple Tkinter UI
- 🍎 PyInstaller macOS App Bundle + DMG installer


### Features

- Realtime speech-to-text using Whisper
- Automatic Japanese translation
- Noise reduction toggle
- Silence threshold (commit level) control
- Audio input device switching
- Tkinter GUI
- macOS app / DMG packaging

### Run (Development)
```
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m venv venv
source venv/bin/activate  
pip install -r requirements.txt   
python gpt.py
```

### Create App Icon
```
mkdir icon.iconset

sips -z 16 16     icon_1024.png --out icon.iconset/icon_16x16.png
sips -z 32 32     icon_1024.png --out icon.iconset/icon_16x16@2x.png
sips -z 32 32     icon_1024.png --out icon.iconset/icon_32x32.png
sips -z 64 64     icon_1024.png --out icon.iconset/icon_32x32@2x.png
sips -z 128 128   icon_1024.png --out icon.iconset/icon_128x128.png
sips -z 256 256   icon_1024.png --out icon.iconset/icon_128x128@2x.png
sips -z 256 256   icon_1024.png --out icon.iconset/icon_256x256.png
sips -z 512 512   icon_1024.png --out icon.iconset/icon_256x256@2x.png
sips -z 512 512   icon_1024.png --out icon.iconset/icon_512x512.png

cp icon_1024.png icon.iconset/icon_512x512@2x.png

iconutil -c icns icon.iconset
mv icon.icns app_icon.icns
```

### Build macOS App (PyInstaller)

```
pyinstaller gpt.py \
  --windowed \
  --name "PepeTranslator" \
  --icon=assets/app_icon.icns \
  --osx-bundle-identifier "jp.agran.pepe.translator"
```
#### Add microphone permission:
Modify PepeTranslator.spec 
```
app = BUNDLE(
    ...,
    bundle_identifier="jp.agran.pepe.translator",
    info_plist={
        "NSMicrophoneUsageDescription": "This app requires access to the microphone for realtime translation.",
    },
)
```


#### Rebuild using the spec:
```
pyinstaller PepeTranslator.spec
```

#### Create DMG Installer:
```
create-dmg \
  --volname "PepeTranslator" \
  --volicon "assets/app_icon.icns" \
  --window-size 500 300 \
  --icon-size 128 \
  --icon "PepeTranslator.app" 120 150 \
  --app-drop-link 380 150 \
  PepeTranslator.dmg \
  dist/PepeTranslator.app
```
