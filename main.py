# flappy_gui_scroll_v4.py — fond défilant, oiseau & tuyaux plus petits, tuyaux du haut retournés, oiseau incliné

import sys
import threading
import queue
import time
import random
import tkinter as tk
from PIL import Image, ImageTk  # pour gérer les rotations/redimensionnements

try:
    import serial
    SERIAL_AVAILABLE = True
except Exception:
    SERIAL_AVAILABLE = False

# ---------- Config ----------
FPS = 60
WIDTH, HEIGHT = 400, 600
GRAVITY = 900.0
FLAP_VY = -320.0
PIPE_GAP = 160
PIPE_SPEED = 140.0
PIPE_INTERVAL = 1.5
SERIAL_BAUD = 115200
BTN_MSG = b'BTN'
# ----------------------------

class SerialReader(threading.Thread):
    def __init__(self, port, baud, out_queue):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.outq = out_queue
        self._stop = threading.Event()
        self.ser = None

    def run(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.1)
        except Exception as e:
            print(f"[SerialReader] cannot open {self.port}: {e}")
            return
        buf = b''
        while not self._stop.is_set():
            try:
                data = self.ser.read(64)
                if data:
                    buf += data
                    if BTN_MSG in buf:
                        self.outq.put('BTN')
                        buf = b''
                    if len(buf) > 1024:
                        buf = b''
            except Exception as e:
                print("[SerialReader] read error:", e)
                break
        if self.ser and self.ser.is_open:
            self.ser.close()

    def stop(self):
        self._stop.set()


class FlappyApp:
    def __init__(self, root, serial_port=None):
        self.root = root
        self.root.title("FLAPIC-BIRD 🐦")
        self.canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT)
        self.canvas.pack()

        # --- Load and resize images using Pillow ---
        bg_base = Image.open("background.png")
        bg_resized = bg_base.resize((WIDTH, HEIGHT), Image.LANCZOS)
        self.bg_img = ImageTk.PhotoImage(bg_resized)

        # Pipes - plus petits et top flipped
        pipe_base = Image.open("pipe.png")
        pipe_small = pipe_base.resize((pipe_base.width // 3, pipe_base.height // 3), Image.LANCZOS)
        self.pipe_img = ImageTk.PhotoImage(pipe_small)
        self.pipe_img_top = ImageTk.PhotoImage(pipe_small.transpose(Image.FLIP_TOP_BOTTOM))

        # Bird - plus petit
        bird_base = Image.open("bird.png")
        bird_small = bird_base.resize((bird_base.width // 8, bird_base.height // 8), Image.LANCZOS)
        self.bird_base_img = bird_small  # on garde l'image PIL pour rotation
        self.bird_img = ImageTk.PhotoImage(self.bird_base_img)

        # Background scroll vars
        self.bg_scroll_x = 0
        self.bg_scroll_speed = 60  # px/s

        # --- Game state ---
        self.state = 'menu'
        self.last_time = time.time()
        self.queue = queue.Queue()
        self.serial_thread = None
        self.recorded_presses = []
        self.replay_index = 0

        if serial_port and SERIAL_AVAILABLE:
            self.serial_thread = SerialReader(serial_port, SERIAL_BAUD, self.queue)
            self.serial_thread.start()
        elif serial_port and not SERIAL_AVAILABLE:
            print("[Main] pyserial not installed; running in keyboard fallback.")

        self.root.bind('<space>', lambda e: self.queue.put('BTN'))

        self.reset_game_vars()
        self.txt_id = None
        self.draw_menu()
        self.running = True
        self.root.after(int(1000 / FPS), self.loop)

    def reset_game_vars(self):
        self.bird_y = HEIGHT / 2
        self.bird_vy = 0.0
        self.pipes = []
        self.pipe_timer = 0.0
        self.score = 0
        self.bg_scroll_x = 0
        self.game_start_time = None

    def draw_menu(self):
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.bg_img, anchor='nw')
        self.canvas.create_text(WIDTH / 2, HEIGHT * 0.2, text="FLAPIC-BIRD", font=("Helvetica", 28, "bold"), fill="white")
        now = time.time()
        if int(now) % 2 == 0:
            self.canvas.create_text(WIDTH / 2, HEIGHT * 0.4, text="Press Start", font=("Helvetica", 20), fill="white")
        self.canvas.create_text(WIDTH / 2, HEIGHT * 0.6, text="Press Button or Space", font=("Helvetica", 12), fill="white")

    def handle_button(self):
        if self.state == 'menu':
            self.start_game()
        elif self.state == 'play':
            self.bird_vy = FLAP_VY
            if self.game_start_time:
                t = time.time() - self.game_start_time
                self.recorded_presses.append(t)
        elif self.state == 'gameover':
            self.start_game()

    def start_game(self):
        self.reset_game_vars()
        self.state = 'play'
        self.game_start_time = time.time()
        for i in range(3):
            self.pipes.append({'x': WIDTH + i * (PIPE_INTERVAL * PIPE_SPEED + 60), 'gap_y': HEIGHT * 0.5})

    def update_physics(self, dt):
        self.bird_vy += GRAVITY * dt
        self.bird_y += self.bird_vy * dt

        for p in self.pipes:
            p['x'] -= PIPE_SPEED * dt
        self.pipes = [p for p in self.pipes if p['x'] + self.pipe_img.width() > 0]
        if len(self.pipes) == 0 or (self.pipes[-1]['x'] < WIDTH - (PIPE_SPEED * PIPE_INTERVAL)):
            gap_y = random.randint(100, HEIGHT - PIPE_GAP - 100)
            self.pipes.append({'x': WIDTH, 'gap_y': gap_y})

        self.bg_scroll_x -= self.bg_scroll_speed * dt
        if self.bg_scroll_x <= -self.bg_img.width():
            self.bg_scroll_x += self.bg_img.width()

    def check_collision(self):
        if self.bird_y <= 0 or self.bird_y >= HEIGHT:
            return True
        bx1 = WIDTH * 0.25 - 10
        bx2 = WIDTH * 0.25 + 10
        for p in self.pipes:
            px1 = p['x']
            px2 = p['x'] + self.pipe_img.width()
            gap_top = p['gap_y']
            gap_bottom = p['gap_y'] + PIPE_GAP
            if not (bx2 < px1 or bx1 > px2):
                if self.bird_y < gap_top or self.bird_y > gap_bottom:
                    return True
            if px2 < bx1 and not p.get('scored'):
                p['scored'] = True
                self.score += 1
        return False

    def draw_background(self):
        x1 = int(self.bg_scroll_x)
        x2 = x1 + self.bg_img.width()
        self.canvas.create_image(x1, 0, image=self.bg_img, anchor='nw')
        self.canvas.create_image(x2, 0, image=self.bg_img, anchor='nw')

    def draw_game(self):
        self.canvas.delete("all")
        self.draw_background()

        # Draw pipes
        for p in self.pipes:
            x = p['x']
            gy = p['gap_y']
            self.canvas.create_image(x, gy - self.pipe_img_top.height(), image=self.pipe_img_top, anchor='nw')
            self.canvas.create_image(x, gy + PIPE_GAP, image=self.pipe_img, anchor='nw')

        # Draw bird with rotation according to velocity
        bx = WIDTH * 0.25
        by = self.bird_y
        angle = max(-45, min(45, -self.bird_vy / 6))  # ajuste facteur pour inclinaison réaliste
        rotated_bird = self.bird_base_img.rotate(angle, resample=Image.BICUBIC, expand=True)
        self.bird_img = ImageTk.PhotoImage(rotated_bird)
        self.canvas.create_image(bx, by, image=self.bird_img)

        # Score
        self.canvas.create_text(WIDTH / 2, 30, text=f"Score: {self.score}", font=("Helvetica", 16, "bold"), fill="white")

    def draw_gameover(self):
        self.canvas.create_text(WIDTH / 2, HEIGHT / 2 - 20, text="GAME OVER", font=("Helvetica", 24, "bold"), fill="white")
        self.canvas.create_text(WIDTH / 2, HEIGHT / 2 + 20, text=f"Score: {self.score}", font=("Helvetica", 16), fill="white")
        self.canvas.create_text(WIDTH / 2, HEIGHT / 2 + 60, text="Press Start to play again", font=("Helvetica", 12), fill="white")

    def loop(self):
        now = time.time()
        dt = now - self.last_time
        self.last_time = now

        while True:
            try:
                ev = self.queue.get_nowait()
            except queue.Empty:
                break
            if ev == 'BTN':
                self.handle_button()

        if self.state == 'menu':
            self.draw_menu()
        elif self.state == 'play':
            self.update_physics(dt)
            collided = self.check_collision()
            self.draw_game()
            if collided:
                self.state = 'gameover'
        elif self.state == 'gameover':
            self.draw_game()
            self.draw_gameover()

        if self.running:
            self.root.after(int(1000 / FPS), self.loop)

    def stop(self):
        self.running = False
        if self.serial_thread:
            self.serial_thread.stop()


if __name__ == "__main__":
    port = None
    if len(sys.argv) >= 2:
        port = sys.argv[1]
    root = tk.Tk()
    app = FlappyApp(root, serial_port=port)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
