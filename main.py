
import sys
import threading
import queue
import time
import random
import tkinter as tk
from PIL import Image, ImageTk

try:
    import serial
    SERIAL_AVAILABLE = True
except Exception:
    SERIAL_AVAILABLE = False


FPS = 60
WIDTH, HEIGHT = 400, 600
GRAVITY = 900.0
FLAP_VY = -320.0
PIPE_GAP = 160
PIPE_SPEED = 140.0
PIPE_INTERVAL = 1.5
SERIAL_BAUD = 115200
DEFAULT_SERIAL_PORT = "COM4"   # change if needed
INPUT_DEVICES = ["Push Button", "Infrared Sensor", "Digital Encoder", "Ultrasound Sensor"]



class SerialReader(threading.Thread):
    """Thread lisant la série et poussant (msg, val) dans outq"""
    def __init__(self, port, baud, out_queue):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.outq = out_queue
        self._stop = threading.Event()
        self.ser = None

    def run(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.05)
            print(f"[SerialReader] opened {self.port} @ {self.baud}")
        except Exception as e:
            print(f"[SerialReader] cannot open {self.port}: {e}")
            return

        buf = b''
        while not self._stop.is_set():
            try:
                data = self.ser.read(128)
                if data:
                    buf += data
                    parts = buf.split(b'\n')
                    buf = parts[-1]  # remainder
                    for raw in parts[:-1]:
                        line = raw.decode(errors='ignore').strip()
                        if not line:
                            continue
                        if line == "BTN":
                            self.outq.put(("BTN", None))
                        elif line == "BTN1":
                            self.outq.put(("BTN1", None))
                        elif line == "BTN2":
                            self.outq.put(("BTN2", None))
                        elif line.startswith("IR:"):
                            try:
                                v = int(line.split(":", 1)[1])
                                self.outq.put(("IR", v))
                            except:
                                pass
                        elif line.startswith("ENC:"):
                            try:
                                v = int(line.split(":", 1)[1])
                                self.outq.put(("ENC", v))
                            except:
                                pass
                        elif line.startswith("ULTRA:") or line.startswith("US:"):
                            try:
                                v = int(line.split(":", 1)[1])
                                self.outq.put(("ULTRA", v))
                            except:
                                pass
                        elif line.startswith("MAX:"):
                            try:
                                v = int(line.split(":", 1)[1])
                                self.outq.put(("MAX", v))
                            except:
                                pass
            except Exception as e:
                print("[SerialReader] read error:", e)
                break

        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except:
            pass

    def stop(self):
        self._stop.set()


class FlappyApp:
    def __init__(self, root, serial_port=None):
        self.root = root
        self.root.title("FLAPIC-BIRD 🐦")
        self.canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT)
        self.canvas.pack()

        # --- Load images ---
        try:
            bg_base = Image.open("background.jpeg")
        except Exception:
            bg_base = Image.new("RGB", (WIDTH, HEIGHT), (135, 206, 235))
        bg_ratio = bg_base.width / bg_base.height
        bg_resized = bg_base.resize((int(bg_ratio * HEIGHT), HEIGHT), Image.LANCZOS)
        self.bg_full_pil = bg_resized
        self.bg_full_width = bg_resized.width
        self.bg_full_height = HEIGHT
        self.bg_scroll_x = 0.0

        try:
            pipe_base = Image.open("pipe2.png")
            pipe_small = pipe_base.resize((pipe_base.width // 3, pipe_base.height // 3), Image.LANCZOS)
        except Exception:
            pipe_small = Image.new("RGBA", (60, 300), (34, 139, 34, 255))
        self.pipe_img = ImageTk.PhotoImage(pipe_small)
        self.pipe_img_top = ImageTk.PhotoImage(pipe_small.transpose(Image.FLIP_TOP_BOTTOM))

        try:
            bird_base = Image.open("bird.png")
            bird_small = bird_base.resize((bird_base.width // 8, bird_base.height // 8), Image.LANCZOS)
        except Exception:
            bird_small = Image.new("RGBA", (24, 24), (255, 200, 0, 255))
        self.bird_base_img = bird_small


        self.state = 'menu'
        self.last_time = time.time()
        self.queue = queue.Queue()
        self.serial_thread = None
        self.last_serial_send = 0.0

        self.menu_selection = 0
        self.in_input_menu = False
        self.input_selection = 0
        self.input_device = 0

        self.reset_game_vars()
        self.has_played_once = False
        self.show_instructions = False
        self.best_score = 0
        self.game_over_time = None

        self.ir_value = 15
        self.enc_value = 15
        self.enc_center = None   
        self.ultra_value = 25    

        self.bird_angle = 0.0
        self.test_mode = False

        if serial_port is None and SERIAL_AVAILABLE:
            try:
                import serial.tools.list_ports
                ports = list(serial.tools.list_ports.comports())
                if ports:
                    serial_port = ports[0].device
            except Exception:
                pass

        if serial_port and SERIAL_AVAILABLE:
            try:
                self.serial_thread = SerialReader(serial_port, SERIAL_BAUD, self.queue)
                self.serial_thread.start()
            except Exception as e:
                print("[Main] serial start failed:", e)

        self.root.bind('<Up>', self.key_up)
        self.root.bind('<Down>', self.key_down)
        self.root.bind('<Return>', self.key_enter)
        self.root.bind('<space>', lambda e: self.queue.put(("BTN", None)))
        self.root.bind('c', lambda e: self.toggle_test_mode())
        self.root.bind('<Left>', lambda e: self.modify_sensor_value(-1))
        self.root.bind('<Right>', lambda e: self.modify_sensor_value(1))

        self.running = True
        self.root.after(int(1000 / FPS), self.loop)

    def reset_game_vars(self):
        self.bird_y = HEIGHT / 2
        self.bird_vy = 0.0
        self.pipes = []
        self.pipe_timer = 0.0
        self.score = 0
        self.bg_scroll_x = 0.0
        self.game_start_time = None
        self.bird_angle = 0.0

    def serial_send_status(self, now):
        if not SERIAL_AVAILABLE or not self.serial_thread or not getattr(self.serial_thread, 'ser', None):
            return
        try:
            ser = self.serial_thread.ser
            if not (ser and ser.is_open):
                return
            if now - self.last_serial_send < 0.2:
                return
            state_flag = 1 if self.state == 'play' else 0
            msg = f"{self.score}|{self.best_score}|{self.input_device}|{state_flag}\n"
            ser.write(msg.encode())
            self.last_serial_send = now
            print(f"[TX → PIC] {msg.strip()}")
        except Exception as e:
            print(f"[SerialSend] error: {e}")

    def toggle_test_mode(self):
        self.test_mode = not self.test_mode
        print(f"[TEST MODE] {'ON' if self.test_mode else 'OFF'}")

    def key_up(self, event):
        if self.state == 'play':
            if self.input_device == 1:
                self.ir_value = max(0, self.ir_value - 1); return
            if self.input_device == 2:
                self.enc_value = max(-15, self.enc_value - 1); return
            if self.input_device == 3:
                self.ultra_value = max(0, self.ultra_value - 1); return
        if self.state != 'menu': return
        if not self.in_input_menu:
            self.menu_selection = (self.menu_selection - 1) % 3
            self.show_instructions = False
        else:
            self.input_selection = (self.input_selection - 1) % len(INPUT_DEVICES)

    def key_down(self, event):
        if self.state == 'play':
            if self.input_device == 1:
                self.ir_value = min(30, self.ir_value + 1); return #ICI
            if self.input_device == 2:
                self.enc_value = min(15, self.enc_value + 1); return
            if self.input_device == 3:
                self.ultra_value = min(50, self.ultra_value + 1); return
        if self.state != 'menu': return
        if not self.in_input_menu:
            self.menu_selection = (self.menu_selection + 1) % 3
            self.show_instructions = False
        else:
            self.input_selection = (self.input_selection + 1) % len(INPUT_DEVICES)

    def key_enter(self, event):
        if self.state != 'menu': return
        if not self.in_input_menu:
            if self.menu_selection == 0:
                self.start_game()
            elif self.menu_selection == 1:
                self.in_input_menu = True
            elif self.menu_selection == 2:
                self.show_instructions = not self.show_instructions
        else:
            self.input_device = self.input_selection
            self.in_input_menu = False
            print(f"[INPUT] selected: {INPUT_DEVICES[self.input_device]}")

    def modify_sensor_value(self, delta):
        if self.state != 'play': return
        if self.input_device == 1:
            self.ir_value = max(0, min(30, self.ir_value + delta)) #ICI
        elif self.input_device == 2:
            self.enc_value = max(-15, min(15, self.enc_value + delta))
        elif self.input_device == 3:
            self.ultra_value = max(0, min(50, self.ultra_value + delta))

    def handle_button(self):
        if self.state == 'menu':
            self.key_enter(None)
        elif self.state == 'play':
            if self.input_device == 0:
                self.bird_vy = FLAP_VY
                self.bird_angle = -20.0
        elif self.state == 'gameover':
            self.state = 'menu'

    def start_game(self):
        self.reset_game_vars()
        self.state = 'play'
        self.has_played_once = True
        self.game_start_time = time.time()
        self.game_over_time = None
        self.enc_center = None   
        for i in range(3):
            self.pipes.append({'x': WIDTH + i * (PIPE_INTERVAL * PIPE_SPEED + 60), 'gap_y': HEIGHT * 0.5})

    def update_physics(self, dt):
        if self.input_device in [1, 2, 3]:
            if self.input_device == 1:
                pos_ratio = self.ir_value / 30.0 #ICI

            elif self.input_device == 2:
                if self.enc_center is None:
                    self.enc_center = self.enc_value
                offset = max(-15, min(15, self.enc_value - self.enc_center))
                pos_ratio = (offset + 15) / 30.0

            elif self.input_device == 3:
                pos_ratio = 1.0 - (self.ultra_value / 50.0)

            target_y = pos_ratio * (HEIGHT - 50)
            self.bird_y += (target_y - self.bird_y) * 8 * dt

        else:
            self.bird_vy += GRAVITY * dt
            self.bird_y += self.bird_vy * dt
            target_angle = max(min((self.bird_vy / 400.0) * 60, 60), -20)
            self.bird_angle += (target_angle - self.bird_angle) * 5 * dt

        for p in self.pipes:
            p['x'] -= PIPE_SPEED * dt
        self.pipes = [p for p in self.pipes if p['x'] + self.pipe_img.width() > 0]
        if len(self.pipes) == 0 or (self.pipes[-1]['x'] < WIDTH - (PIPE_SPEED * PIPE_INTERVAL)):
            gap_y = random.randint(100, HEIGHT - PIPE_GAP - 100)
            self.pipes.append({'x': WIDTH, 'gap_y': gap_y})
        self.bg_scroll_x = (self.bg_scroll_x + 60 * dt) % self.bg_full_width

    def check_collision(self):
        if self.test_mode:
            return False
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
        x = int(self.bg_scroll_x) % self.bg_full_width
        visible_w = min(WIDTH, self.bg_full_width - x)
        region = self.bg_full_pil.crop((x, 0, x + visible_w, self.bg_full_height))
        img1 = ImageTk.PhotoImage(region)
        self.canvas.create_image(0, 0, image=img1, anchor='nw')
        if visible_w < WIDTH:
            region2 = self.bg_full_pil.crop((0, 0, WIDTH - visible_w, self.bg_full_height))
            img2 = ImageTk.PhotoImage(region2)
            self.canvas.create_image(visible_w, 0, image=img2, anchor='nw')
            self._bg_imgs = (img1, img2)
        else:
            self._bg_imgs = (img1,)

    def draw_game(self):
        self.canvas.delete("all")
        self.draw_background()
        for p in self.pipes:
            x = p['x']; gy = p['gap_y']
            self.canvas.create_image(x, gy - self.pipe_img_top.height(), image=self.pipe_img_top, anchor='nw')
            self.canvas.create_image(x, gy + PIPE_GAP, image=self.pipe_img, anchor='nw')
        bx = WIDTH * 0.25
        by = self.bird_y
        if self.input_device == 0:
            rotated = self.bird_base_img.rotate(-self.bird_angle, resample=Image.BICUBIC, expand=True)
        else:
            rotated = self.bird_base_img
        self.bird_img = ImageTk.PhotoImage(rotated)
        self.canvas.create_image(bx, by, image=self.bird_img)
        self.canvas.create_text(WIDTH / 2, 28, text=f"Score: {self.score}", font=("Helvetica", 14, "bold"), fill="white")
        self.canvas.create_text(WIDTH - 70, 28, text=f"Best: {self.best_score}", font=("Helvetica", 12), fill="yellow")

        if self.input_device in (1, 2, 3):
            label = INPUT_DEVICES[self.input_device].split()[0]
            if self.input_device == 1:
                val = self.ir_value
            elif self.input_device == 2:
                val = self.enc_value
            else:
                val = self.ultra_value
            self.canvas.create_text(70, 28, text=f"{label}: {val}", font=("Helvetica", 12), fill="cyan")

        if self.test_mode:
            self.canvas.create_text(80, HEIGHT - 18, text="NO COLLISION", font=("Helvetica", 10, "bold"), fill="red")

    def draw_gameover(self):
        self.canvas.delete("all")
        self.draw_background()
        self.canvas.create_text(WIDTH / 2, HEIGHT / 2 - 60, text="GAME OVER", font=("Helvetica", 28, "bold"), fill="white")
        self.canvas.create_text(WIDTH / 2, HEIGHT / 2, text=f"Score: {self.score}", font=("Helvetica", 18, "bold"), fill="white")
        self.canvas.create_text(WIDTH / 2, HEIGHT / 2 + 40, text=f"Best: {self.best_score}", font=("Helvetica", 16), fill="yellow")
        self.canvas.create_text(WIDTH / 2, HEIGHT / 2 + 90, text="Returning to menu...", font=("Helvetica", 12), fill="white")

    def draw_menu(self):
        self.canvas.delete("all")
        self.draw_background()
        self.canvas.create_text(WIDTH / 2, HEIGHT * 0.14, text="FLAPIC-BIRD", font=("Helvetica", 30, "bold"), fill="white")
        self.canvas.create_text(WIDTH - 80, 36, text=f"Best: {self.best_score}", font=("Helvetica", 12), fill="yellow")
        if not self.in_input_menu:
            options = ["Play" if not self.has_played_once else "Replay", "Change Input Device", "Instructions"]
            for i, t in enumerate(options):
                color = "yellow" if i == self.menu_selection else "white"
                self.canvas.create_text(WIDTH / 2, HEIGHT * 0.32 + i * 48, text=t, font=("Helvetica", 18, "bold"), fill=color)
            if self.show_instructions:
                frame_color = "#5A7D7C"
                self.canvas.create_rectangle(30, HEIGHT * 0.6, WIDTH - 30, HEIGHT - 40, fill=frame_color, outline="white", width=2)
                instr = ("Welcome to FLAPIC-Bird!\nFly as far as possible,\navoid the pipes,\ncontrol with PIC or space.")
                self.canvas.create_text(WIDTH / 2, HEIGHT * 0.72, text=instr, font=("Helvetica", 12), fill="white", justify="center")
        else:
            self.canvas.create_text(WIDTH / 2, HEIGHT * 0.22, text="SELECT INPUT DEVICE", font=("Helvetica", 20, "bold"), fill="white")
            for i, dev in enumerate(INPUT_DEVICES):
                color = "yellow" if i == self.input_selection else "white"
                marker = " ←" if i == self.input_device else ""
                self.canvas.create_text(WIDTH / 2, HEIGHT * 0.34 + i * 46, text=dev + marker, font=("Helvetica", 16), fill=color)
            self.canvas.create_text(WIDTH / 2, HEIGHT - 40, text="Use up/down + Enter (or BTN1/BTN2/BTN)", font=("Helvetica", 10), fill="white")

    def loop(self):
        now = time.time()
        dt = now - self.last_time
        self.last_time = now
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            if not isinstance(item, tuple):
                continue
            msg, val = item
            if msg == "BTN":
                self.handle_button()
            elif msg == "BTN1":
                self.key_up(None)
            elif msg == "BTN2":
                self.key_down(None)
            elif msg == "IR":
                self.ir_value = max(0, min(30, val)) #ICI
            elif msg == "ENC":
                self.enc_value = val
            elif msg == "ULTRA":
                self.ultra_value = max(0, min(50, val))
            elif msg == "MAX":
                self.best_score = val
        self.serial_send_status(now)
        if self.state == 'menu':
            self.draw_menu()
        elif self.state == 'play':
            self.update_physics(dt)
            collided = self.check_collision()
            self.draw_game()
            if collided:
                if self.score > self.best_score:
                    self.best_score = self.score
                self.state = 'gameover'
                self.game_over_time = now
        elif self.state == 'gameover':
            self.draw_gameover()
            if now - (self.game_over_time or now) > 3.0:
                self.state = 'menu'
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
