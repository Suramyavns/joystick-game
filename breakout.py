"""
Breakout — controlled by the Arduino Nano Bluetooth joystick.

Controller protocol (from component_test.ino, 9600 baud over HC-05),
5-byte binary packets:
    [0xA5] [x] [y] [button] [x^y^button]     x, y: 0-255, centre ~128

Controls:
    Joystick X axis  -> move paddle (analog: further = faster)
    Joystick button  -> launch ball / restart after game over
    Keyboard fallback: Left/Right arrows, Space, Esc to quit

Usage:
    python breakout.py            # connects to COM9
    python breakout.py COM5       # use a different port
    python breakout.py --keyboard # skip serial, keyboard only
"""

import math
import random
import sys
import threading
import time

import pygame

# ---------------------------------------------------------------- controller

SERIAL_PORT = "COM9"
BAUD = 9600
INVERT_X = False          # set True if the paddle moves the wrong way
SYNC = 0xA5               # packet: [SYNC] [x] [y] [button] [x^y^button]


class Controller:
    """Reads the joystick over Bluetooth serial in a background thread.

    Keeps the latest stick position; reconnects automatically if the
    HC-05 link drops or refuses the first open (it does that sometimes).
    """

    def __init__(self, port):
        self.port = port
        self.x = 128                 # centre (0-255)
        self.button = False
        self.connected = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def axis(self):
        """Stick deflection as -1.0 .. +1.0 with deadzone applied."""
        n = (self.x - 128) / 128.0
        if INVERT_X:
            n = -n
        return 0.0 if abs(n) < 0.10 else max(-1.0, min(1.0, n))

    def _run(self):
        import serial
        buf = b""
        while not self._stop.is_set():
            try:
                # timeout must be tiny: read() blocks until it gets
                # something, and every ms it waits is input lag.
                with serial.Serial(self.port, BAUD, timeout=0.005) as ser:
                    self.connected = True
                    ser.reset_input_buffer()   # drop any stale backlog
                    buf = b""
                    while not self._stop.is_set():
                        chunk = ser.read(ser.in_waiting or 1)
                        if not chunk:
                            continue
                        buf += chunk
                        # scan for 5-byte packets: SYNC x y btn checksum
                        while True:
                            i = buf.find(SYNC)
                            if i < 0:
                                buf = b""
                                break
                            if len(buf) - i < 5:
                                buf = buf[i:]
                                break
                            x, y, btn, chk = buf[i + 1:i + 5]
                            if btn <= 1 and (x ^ y ^ btn) == chk:
                                self.x = x
                                self.button = btn == 1
                                buf = buf[i + 5:]
                            else:
                                buf = buf[i + 1:]   # false sync byte
            except serial.SerialException:
                self.connected = False
                time.sleep(2)            # retry — HC-05 opens are flaky
        self.connected = False


class KeyboardController:
    """Fallback with the same interface as Controller."""
    port = "keyboard"
    connected = True
    button = False

    def stop(self):
        pass

    def axis(self):
        keys = pygame.key.get_pressed()
        return (keys[pygame.K_RIGHT] - keys[pygame.K_LEFT]) * 1.0


# ---------------------------------------------------------------- game

WIDTH, HEIGHT = 800, 600
PADDLE_W, PADDLE_H = 135, 14
PADDLE_SPEED = 900            # px/s at full stick deflection
BALL_R = 8
BALL_SPEED = 330              # px/s starting speed
SPEEDUP = 1.02                # per paddle hit
MAX_SPEED = 680
BRICK_ROWS, BRICK_COLS = 6, 10
BRICK_H = 24
LIVES = 3

ROW_COLORS = [(231, 76, 60), (230, 126, 34), (241, 196, 15),
              (46, 204, 113), (52, 152, 219), (155, 89, 182)]
ROW_POINTS = [60, 50, 40, 30, 20, 10]

BG = (16, 18, 28)
FG = (235, 235, 240)
DIM = (130, 135, 150)


def make_bricks():
    gap = 4
    brick_w = (WIDTH - gap * (BRICK_COLS + 1)) / BRICK_COLS
    bricks = []
    for row in range(BRICK_ROWS):
        for col in range(BRICK_COLS):
            rect = pygame.Rect(gap + col * (brick_w + gap),
                               70 + row * (BRICK_H + gap),
                               brick_w, BRICK_H)
            bricks.append((rect, ROW_COLORS[row], ROW_POINTS[row]))
    return bricks


def reflect_off_paddle(ball_vel, ball_x, paddle):
    """Bounce angle depends on where the ball hits the paddle."""
    speed = min(pygame.Vector2(ball_vel).length() * SPEEDUP, MAX_SPEED)
    offset = (ball_x - paddle.centerx) / (paddle.width / 2)   # -1 .. 1
    offset = max(-0.95, min(0.95, offset))
    angle = offset * 60                                        # degrees
    vel = pygame.Vector2(0, -speed).rotate(angle)
    return vel


def main():
    args = sys.argv[1:]
    if "--keyboard" in args:
        ctl = KeyboardController()
    else:
        port = next((a for a in args if not a.startswith("-")), SERIAL_PORT)
        ctl = Controller(port)

    selftest = "--selftest" in args

    pygame.init()
    # SCALED keeps the game logic at 800x600 and stretches the picture
    # to whatever size the window is; RESIZABLE allows maximizing.
    screen = pygame.display.set_mode((WIDTH, HEIGHT),
                                     pygame.SCALED | pygame.RESIZABLE)
    pygame.display.set_caption("Bluetooth Breakout")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 22)
    big = pygame.font.SysFont("consolas", 48, bold=True)

    def text(s, f, color, center):
        surf = f.render(s, True, color)
        screen.blit(surf, surf.get_rect(center=center))

    paddle = pygame.Rect(0, 0, PADDLE_W, PADDLE_H)
    ball_pos = pygame.Vector2()
    ball_vel = pygame.Vector2()

    score = 0
    lives = LIVES
    level = 1
    bricks = make_bricks()
    state = "ready"            # ready | playing | dead | gameover | win
    paddle.midbottom = (WIDTH // 2, HEIGHT - 30)
    prev_button = False
    frames = 0
    axis_smooth = 0.0
    paddle_x = float(paddle.x)   # sub-pixel position (Rect.x truncates)

    def reset_ball():
        ball_pos.update(paddle.centerx, paddle.top - BALL_R)
        ball_vel.update(0, 0)

    reset_ball()

    running = True
    while running:
        dt = clock.tick(60) / 1000.0
        frames += 1
        if selftest and frames > 120:
            running = False

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
                pygame.display.toggle_fullscreen()

        # --- input: joystick button edge, or Space ---
        keys = pygame.key.get_pressed()
        button = ctl.button or keys[pygame.K_SPACE]
        fire = button and not prev_button
        prev_button = button

        # --- paddle ---
        target = ctl.axis()
        # gentle response curve: fine control near centre, quick at the edge
        target = math.copysign(abs(target) ** 1.5, target)
        # light low-pass filter: irons out bursty Bluetooth updates
        axis_smooth += (target - axis_smooth) * min(1.0, dt * 30.0)
        paddle_x += axis_smooth * PADDLE_SPEED * dt
        paddle_x = max(0.0, min(paddle_x, WIDTH - paddle.width))
        paddle.x = round(paddle_x)

        if state == "ready":
            reset_ball()
            if fire or selftest:
                angle = random.uniform(-40, 40)
                ball_vel.update(pygame.Vector2(0, -BALL_SPEED).rotate(angle))
                state = "playing"

        elif state == "playing":
            ball_pos += ball_vel * dt

            # walls
            if ball_pos.x < BALL_R:
                ball_pos.x = BALL_R
                ball_vel.x = abs(ball_vel.x)
            elif ball_pos.x > WIDTH - BALL_R:
                ball_pos.x = WIDTH - BALL_R
                ball_vel.x = -abs(ball_vel.x)
            if ball_pos.y < BALL_R:
                ball_pos.y = BALL_R
                ball_vel.y = abs(ball_vel.y)

            # paddle
            ball_rect = pygame.Rect(ball_pos.x - BALL_R, ball_pos.y - BALL_R,
                                    BALL_R * 2, BALL_R * 2)
            if ball_vel.y > 0 and ball_rect.colliderect(paddle):
                ball_pos.y = paddle.top - BALL_R
                ball_vel.update(reflect_off_paddle(ball_vel, ball_pos.x, paddle))

            # bricks
            hit_idx = ball_rect.collidelist([b[0] for b in bricks])
            if hit_idx >= 0:
                rect, _, points = bricks.pop(hit_idx)
                score += points
                # flip axis with the smaller overlap
                dx = min(ball_rect.right - rect.left, rect.right - ball_rect.left)
                dy = min(ball_rect.bottom - rect.top, rect.bottom - ball_rect.top)
                if dx < dy:
                    ball_vel.x = -ball_vel.x
                else:
                    ball_vel.y = -ball_vel.y
                if not bricks:
                    level += 1
                    state = "win"

            # dropped
            if ball_pos.y > HEIGHT + BALL_R:
                lives -= 1
                state = "gameover" if lives == 0 else "ready"

        elif state == "win":
            if fire:
                bricks = make_bricks()
                state = "ready"

        elif state == "gameover":
            if fire:
                score, lives, level = 0, LIVES, 1
                bricks = make_bricks()
                state = "ready"

        # ------------------------------------------------------- draw
        screen.fill(BG)
        for rect, color, _ in bricks:
            pygame.draw.rect(screen, color, rect, border_radius=4)
        pygame.draw.rect(screen, FG, paddle, border_radius=7)
        pygame.draw.circle(screen, (255, 210, 90),
                           (int(ball_pos.x), int(ball_pos.y)), BALL_R)

        screen.blit(font.render(f"Score {score}", True, FG), (12, 10))
        screen.blit(font.render(f"Level {level}", True, DIM), (WIDTH // 2 - 40, 10))
        lives_s = font.render("♥ " * lives, True, (231, 76, 60))
        screen.blit(lives_s, (WIDTH - lives_s.get_width() - 12, 10))

        if not ctl.connected:
            text(f"controller not connected ({ctl.port}) — retrying, arrows work too",
                 font, (230, 126, 34), (WIDTH // 2, HEIGHT - 12))
        if state == "ready":
            text("press the joystick button to launch", font, DIM,
                 (WIDTH // 2, HEIGHT // 2 + 60))
        elif state == "win":
            text("LEVEL CLEARED!", big, (46, 204, 113), (WIDTH // 2, HEIGHT // 2))
            text("press the button for the next round", font, DIM,
                 (WIDTH // 2, HEIGHT // 2 + 50))
        elif state == "gameover":
            text("GAME OVER", big, (231, 76, 60), (WIDTH // 2, HEIGHT // 2))
            text(f"final score {score} — press the button to restart", font, DIM,
                 (WIDTH // 2, HEIGHT // 2 + 50))

        pygame.display.flip()

    ctl.stop()
    pygame.quit()
    if selftest:
        print(f"selftest OK: {frames} frames, score={score}, state={state}")


if __name__ == "__main__":
    main()
