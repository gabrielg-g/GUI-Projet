# flappy_gui.py
# Flappy-like GUI controlled by a single push button sent over serial (or spacebar fallback).
# Dependencies: pyserial
# Usage: python flappy_gui.py [SERIAL_PORT]
import sys
import threading
import queue
import time
import random
import math
import tkinter as tk
from tkinter import messagebox

try:
    import serial
    SERIAL_AVAILABLE = True
except Exception:
    SERIAL_AVAILABLE = False

# ---------- Config ----------
FPS = 60
WIDTH, HEIGHT = 400, 600
BIRD_SIZE = 24
GRAVITY = 900.0        # px/s^2
FLAP_VY = -320.0       # px/s initial velocity on flap
PIPE_WIDTH = 60
PIPE_GAP = 160
PIPE_SPEED = 140.0     # px/s
PIPE_INTERVAL = 1.5    # seconds
SERIAL_BAUD = 115200
BTN_MSG = b'BTN'       # expected substring from MCU
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
                    # simple parse: if BTN present, push event
                    if BTN_MSG in buf:
                        self.outq.put('BTN')
                        buf = b''
                    # drop long buffer
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
        self.root.title("FLAPIC-BIRD - First Defense (Push Button)")
        self.canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, bg='skyblue')
        self.canvas.pack()
        self.state = 'menu'  # menu, play, gameover, replay
        self.last_time = time.time()
        self.queue = queue.Queue()
        self.serial_thread = None
        self.recorded_presses = []  # timestamps relative to game start (for replay)
        self.replay_index = 0

        # start serial reader if requested
        if serial_port and SERIAL_AVAILABLE:
            self.serial_thread = SerialReader(serial_port, SERIAL_BAUD, self.queue)
            self.serial_thread.start()
            print("[Main] Serial thread started on", serial_port)
        elif serial_port and not SERIAL_AVAILABLE:
            print("[Main] pyserial not installed; running in keyboard fallback.")

        # bind keyboard fallback
        self.root.bind('<space>', lambda e: self.queue.put('BTN'))
        # menu blinking
        self.blink = True
        self.blink_timer = 0.0

        # game state
        self.reset_game_vars()

        # UI text ids
        self.txt_id = None
        self.draw_menu()
        self.running = True
        self.root.after(int(1000 / FPS), self.loop)

    def reset_game_vars(self):
        self.bird_y = HEIGHT/2
        self.bird_vy = 0.0
        self.pipes = []  # list of dict {x, gap_y}
        self.pipe_timer = 0.0
        self.score = 0
        self.game_start_time = None

    def draw_menu(self):
        self.canvas.delete("all")
        self.canvas.create_text(WIDTH/2, HEIGHT*0.2, text="FLAPIC-BIRD", font=("Helvetica", 28, "bold"))
        # blinking "Press Start"
        now = time.time()
        if int(now) % 2 == 0:
            self.canvas.create_text(WIDTH/2, HEIGHT*0.4, text="Press Start", font=("Helvetica", 20))
        self.canvas.create_text(WIDTH/2, HEIGHT*0.6, text="Modes: Push Button (first defense)", font=("Helvetica", 12))
        self.canvas.create_text(WIDTH/2, HEIGHT*0.75, text="Controls: push the physical button or press SPACE", font=("Helvetica", 10))
        self.canvas.create_text(WIDTH/2, HEIGHT*0.9, text="Play  Instructions  Replay", font=("Helvetica", 12))

    def spawn_pipe(self):
        gap_y = 120 + (HEIGHT - 240) * 0.5  # centered gap; you can randomize later
        self.pipes.append({'x': WIDTH + PIPE_WIDTH, 'gap_y': gap_y})

    def handle_button(self):
    # Bouton pressé : agit selon l'état du jeu
        if self.state == 'menu':
            # Démarre une nouvelle partie depuis le menu
            self.start_game()
            return

        if self.state == 'play':
            # Fait voler l'oiseau
            self.bird_vy = FLAP_VY
            # Enregistre le moment du flap (utile pour replay)
            if self.game_start_time is not None:
                t = time.time() - self.game_start_time
                self.recorded_presses.append(t)
            return

        if self.state == 'gameover':
            # Après une perte, appuyer sur le bouton relance la partie
            self.start_game()
            return


    def start_game(self):
        self.reset_game_vars()
        self.state = 'play'
        self.game_start_time = time.time()
        self.pipe_timer = 0.0
        self.score = 0
        self.pipes = []
        # spawn initial pipes
        for i in range(3):
            self.pipes.append({'x': WIDTH + i * (PIPE_INTERVAL*PIPE_SPEED + 60), 'gap_y': HEIGHT*0.5})

    
    def update_physics(self, dt):
        # --- Gravité et mouvement de l'oiseau ---
        self.bird_vy += GRAVITY * dt
        self.bird_y += self.bird_vy * dt

        # --- Déplacement des tuyaux ---
        for p in self.pipes:
            p['x'] -= PIPE_SPEED * dt

        # --- Suppression des tuyaux sortis de l'écran ---
        self.pipes = [p for p in self.pipes if p['x'] + PIPE_WIDTH > 0]

        # --- Génération d'un nouveau tuyau si le dernier est assez loin ---
        if len(self.pipes) == 0 or (self.pipes[-1]['x'] < WIDTH - (PIPE_SPEED * PIPE_INTERVAL)):
            # Hauteur du trou : variation aléatoire raisonnable (entre 100 et HEIGHT-PIPE_GAP-100)
            gap_y = random.randint(100, HEIGHT - PIPE_GAP - 100)
            self.pipes.append({'x': WIDTH, 'gap_y': gap_y})

    def check_collision(self):
        # floor/ceiling
        if self.bird_y - BIRD_SIZE/2 <= 0 or self.bird_y + BIRD_SIZE/2 >= HEIGHT:
            return True
        bx1 = WIDTH*0.25 - BIRD_SIZE/2
        bx2 = WIDTH*0.25 + BIRD_SIZE/2
        for p in self.pipes:
            px1 = p['x']
            px2 = p['x'] + PIPE_WIDTH
            gap_top = p['gap_y']
            gap_bottom = p['gap_y'] + PIPE_GAP
            # collision if bird overlaps pipe rect and not within gap
            if not (bx2 < px1 or bx1 > px2):
                # horizontally overlapping
                if self.bird_y - BIRD_SIZE/2 < gap_top or self.bird_y + BIRD_SIZE/2 > gap_bottom:
                    return True
            # scoring: bird passed pipe
            if px2 < bx1 and not p.get('scored'):
                p['scored'] = True
                self.score += 1
        return False

    def draw_game(self):
        self.canvas.delete("all")
        # background
        self.canvas.create_rectangle(0, 0, WIDTH, HEIGHT, fill='skyblue', outline='')
        # bird
        bx = WIDTH*0.25
        by = self.bird_y
        angle = max(-45, min(45, -self.bird_vy / 6))
        self.canvas.create_oval(bx-BIRD_SIZE/2, by-BIRD_SIZE/2, bx+BIRD_SIZE/2, by+BIRD_SIZE/2, fill='yellow', outline='black')
        # pipes
        for p in self.pipes:
            x = p['x']
            gy = p['gap_y']
            # top
            self.canvas.create_rectangle(x, -10, x+PIPE_WIDTH, gy, fill='green', outline='black')
            # bottom
            self.canvas.create_rectangle(x, gy+PIPE_GAP, x+PIPE_WIDTH, HEIGHT+10, fill='green', outline='black')
        # score
        self.canvas.create_text(WIDTH/2, 30, text=f"Score: {self.score}", font=("Helvetica", 16, "bold"))

    def draw_gameover(self):
        self.canvas.create_text(WIDTH/2, HEIGHT/2-20, text="GAME OVER", font=("Helvetica", 24, "bold"))
        self.canvas.create_text(WIDTH/2, HEIGHT/2+20, text=f"Score: {self.score}", font=("Helvetica", 16))
        self.canvas.create_text(WIDTH/2, HEIGHT/2+60, text="Press Start to play again", font=("Helvetica", 12))

    def loop(self):
        now = time.time()
        dt = now - self.last_time
        self.last_time = now
        # process serial / queue
        while True:
            try:
                ev = self.queue.get_nowait()
            except queue.Empty:
                break
            if ev == 'BTN':
                self.handle_button()
        # game state updates
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
        # schedule next frame
        if self.running:
            self.root.after(int(1000/FPS), self.loop)

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
