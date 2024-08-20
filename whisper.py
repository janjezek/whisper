import sys
import logging
import threading
import pyaudio
import wave
import requests
import pyperclip
from pynput import keyboard
from pynput.keyboard import Key, Controller
import time
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import os
from dotenv import load_dotenv
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import QObject, pyqtSignal, QTimer

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Audio Parameters
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100
CHUNK = 1024
WAVE_OUTPUT_FILENAME = "output.wav"

# Global Variables
recording = False
frames = []
lock = threading.Lock()

class AudioHandler:
    def __init__(self):
        self.p = None
        self.stream = None

    def __enter__(self):
        self.p = pyaudio.PyAudio()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        if self.p:
            self.p.terminate()

    def start_stream(self):
        self.stream = self.p.open(format=FORMAT, channels=CHANNELS,
                                  rate=RATE, input=True,
                                  frames_per_buffer=CHUNK)

def start_recording():
    global frames, recording
    frames = []
    with lock:
        recording = True
    
    with AudioHandler() as audio:
        audio.start_stream()
        while recording:
            try:
                data = audio.stream.read(CHUNK, exception_on_overflow=False)
                frames.append(data)
            except IOError as e:
                logging.warning(f"IOError during recording: {e}")

def stop_recording():
    global recording
    with lock:
        recording = False
    logging.info("Recording stopped")

def save_recording():
    global frames
    try:
        logging.info("Saving recording")
        if not frames:
            logging.warning("No frames to save")
            return
        with AudioHandler() as audio:
            wf = wave.open(WAVE_OUTPUT_FILENAME, 'wb')
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(audio.p.get_sample_size(FORMAT))
            wf.setframerate(RATE)
            wf.writeframes(b''.join(frames))
            wf.close()
        logging.info("Recording saved")
    except Exception as e:
        logging.error(f"Error saving recording: {e}")
    finally:
        frames = []

def transcribe_audio():
    try:
        logging.info("Starting transcription")
        url = "https://api.openai.com/v1/audio/transcriptions"
        headers = {
            "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"
        }
        
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session = requests.Session()
        session.mount("https://", adapter)
        
        with open(WAVE_OUTPUT_FILENAME, "rb") as f:
            files = {"file": (WAVE_OUTPUT_FILENAME, f, "audio/wav")}
            data = {"model": "whisper-1"}
            logging.info("Sending request to OpenAI API")
            
            for attempt in range(3):
                try:
                    response = session.post(url, headers=headers, files=files, data=data)
                    logging.info(f"API Response status code: {response.status_code}")
                    response.raise_for_status()
                    transcription = response.json()['text']
                    logging.info(f"Transcription: {transcription}")
                    return transcription
                except requests.exceptions.RequestException as e:
                    logging.error(f"Request error in transcription (attempt {attempt + 1}): {e}")
                    if attempt < 2:
                        time.sleep(2 ** attempt)  # Exponential backoff
                    else:
                        return "Transcription failed after multiple attempts."
    except Exception as e:
        logging.error(f"Unexpected error in transcription: {e}")
        return "Transcription failed due to unexpected error."

class TrayIcon(QSystemTrayIcon):
    def __init__(self, app):
        super().__init__(app)
        self.setIcon(QIcon("path_to_your_icon.png"))  # Replace with path to your icon
        self.setVisible(True)

        menu = QMenu()
        exit_action = menu.addAction("Exit")
        exit_action.triggered.connect(app.quit)
        self.setContextMenu(menu)

    def update_icon(self, is_recording):
        if is_recording:
            self.setIcon(QIcon("path_to_recording_icon.png"))  # Replace with path to your recording icon
        else:
            self.setIcon(QIcon("path_to_normal_icon.png"))  # Replace with path to your normal icon

class RecordingManager(QObject):
    transcription_ready = pyqtSignal(str)

    def __init__(self, tray_icon):
        super().__init__()
        self.tray_icon = tray_icon
        self.transcription_ready.connect(self.handle_transcription)

    def toggle_recording(self):
        global recording
        if not recording:
            threading.Thread(target=start_recording, daemon=True).start()
            self.tray_icon.update_icon(True)
        else:
            stop_recording()
            self.tray_icon.update_icon(False)
            save_recording()
            transcription = transcribe_audio()
            self.transcription_ready.emit(transcription)

    def handle_transcription(self, transcription):
        paste_transcription(transcription)

def paste_transcription(text):
    keyboard = Controller()
    
    # Type out the transcription
    keyboard.type(text)
    
    logging.info("Transcription typed out")

def on_activate():
    recording_manager.toggle_recording()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    tray_icon = TrayIcon(app)
    recording_manager = RecordingManager(tray_icon)

    hotkey = keyboard.HotKey(
        keyboard.HotKey.parse('<ctrl>+<cmd>+h'),
        on_activate)

    with keyboard.Listener(
        on_press=lambda k: hotkey.press(k),
        on_release=lambda k: hotkey.release(k)
    ) as listener:
        logging.info("Voice recognition app is running. Press Ctrl+Cmd+H to start/stop recording.")
        sys.exit(app.exec_())