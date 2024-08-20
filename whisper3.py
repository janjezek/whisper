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
import multiprocessing
import warnings
import urllib3
import os
from dotenv import load_dotenv

# Suppress LibreSSL warning
warnings.filterwarnings("ignore", category=urllib3.exceptions.NotOpenSSLWarning)

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

def create_ssl_context():
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context

def transcribe_audio_process(queue):
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
                    queue.put(transcription)
                    return
                except requests.exceptions.RequestException as e:
                    logging.error(f"Request error in transcription (attempt {attempt + 1}): {e}")
                    if attempt < 2:
                        time.sleep(2 ** attempt)  # Exponential backoff
                    else:
                        queue.put("Transcription failed after multiple attempts.")
        
    except Exception as e:
        logging.error(f"Unexpected error in transcription: {e}")
        queue.put("Transcription failed due to unexpected error.")

def transcribe_audio():
    queue = multiprocessing.Queue()
    process = multiprocessing.Process(target=transcribe_audio_process, args=(queue,))
    process.start()
    process.join(timeout=30)  # Wait for up to 30 seconds
    
    if process.is_alive():
        process.terminate()
        process.join()
        return "Transcription timed out"
    
    if not queue.empty():
        return queue.get()
    else:
        return "Transcription failed"

def copy_and_paste_transcription(text):
    try:
        logging.info("Copying transcription to clipboard")
        pyperclip.copy(text)
        logging.info("Transcription copied to clipboard")

        # Simulate Cmd+V to paste
        keyboard_controller = Controller()
        keyboard_controller.press(Key.cmd)
        keyboard_controller.press('v')
        keyboard_controller.release('v')
        keyboard_controller.release(Key.cmd)
        logging.info("Transcription pasted")
    except Exception as e:
        logging.error(f"Error copying or pasting transcription: {e}")

def on_activate():
    global recording
    if not recording:
        threading.Thread(target=start_recording, daemon=True).start()
    else:
        stop_recording()
        save_recording()
        transcription = transcribe_audio()
        copy_and_paste_transcription(transcription)

hotkey = keyboard.HotKey(
    keyboard.HotKey.parse('<ctrl>+<cmd>+h'),
    on_activate)

def for_canonical(f):
    return lambda k: f(listener.canonical(k))

listener = keyboard.Listener(
    on_press=for_canonical(hotkey.press),
    on_release=for_canonical(hotkey.release))

if __name__ == "__main__":
    try:
        listener.start()
        logging.info("Voice recognition app is running. Press Ctrl+Cmd+H to start/stop recording.")
        listener.join()
    except Exception as e:
        logging.error(f"Error in main thread: {e}")
    finally:
        if recording:
            stop_recording()