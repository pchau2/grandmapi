#!/usr/bin/env python3
import pygame
import sys
import threading
import wifi_manager
from onscreen_keyboard import OnScreenKeyboard

COLORS = {
    "bg": (15, 15, 25),
    "title": (255, 255, 255),
    "subtitle": (160, 160, 180),
    "item": (50, 50, 70),
    "item_selected": (80, 80, 200),
    "item_text": (255, 255, 255),
    "status_ok": (80, 200, 80),
    "status_err": (200, 80, 80),
    "status_info": (180, 180, 80),
}

SCREEN_W, SCREEN_H = 1920, 1080


class App:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.FULLSCREEN)
        pygame.display.set_caption("grandmapi WiFi Setup")
        self.clock = pygame.time.Clock()
        self.font_lg = pygame.font.SysFont("dejavusans", 52)
        self.font_md = pygame.font.SysFont("dejavusans", 38)
        self.font_sm = pygame.font.SysFont("dejavusans", 30)

        self.scene = "scan"       # scan | list | password | connecting | done
        self.networks = []
        self.selected = 0
        self.selected_ssid = ""
        self.status_msg = "Scanning for networks..."
        self.status_color = COLORS["status_info"]
        self.keyboard = None

        self._start_scan()

    def _start_scan(self):
        self.scene = "scan"
        self.status_msg = "Scanning for networks..."
        self.status_color = COLORS["status_info"]

        def scan():
            nets = wifi_manager.scan_networks()
            self.networks = nets if nets else []
            self.selected = 0
            if self.networks:
                self.scene = "list"
            else:
                self.status_msg = "No networks found. Press OK to retry."
                self.status_color = COLORS["status_err"]
                self.scene = "error"

        threading.Thread(target=scan, daemon=True).start()

    def _start_connect(self, ssid, password):
        self.scene = "connecting"
        self.status_msg = f"Connecting to {ssid}..."
        self.status_color = COLORS["status_info"]

        def connect():
            ok = wifi_manager.connect(ssid, password)
            if ok:
                self.status_msg = f"Connected to {ssid}!"
                self.status_color = COLORS["status_ok"]
                self.scene = "done"
            else:
                self.status_msg = "Connection failed. Press OK to retry."
                self.status_color = COLORS["status_err"]
                self.scene = "error"

        threading.Thread(target=connect, daemon=True).start()

    def draw_scan(self):
        self.screen.fill(COLORS["bg"])
        self._draw_title()
        s = self.font_md.render(self.status_msg, True, self.status_color)
        self.screen.blit(s, (SCREEN_W // 2 - s.get_width() // 2, SCREEN_H // 2))

    def draw_list(self):
        self.screen.fill(COLORS["bg"])
        self._draw_title()

        hint = self.font_sm.render("↑↓ Navigate   OK Select   Currently: " + (wifi_manager.current_ssid() or "Not connected"), True, COLORS["subtitle"])
        self.screen.blit(hint, (SCREEN_W // 2 - hint.get_width() // 2, 160))

        item_h = 80
        visible = 8
        start = max(0, self.selected - visible // 2)
        end = min(len(self.networks), start + visible)

        for i, idx in enumerate(range(start, end)):
            ssid = self.networks[idx]
            color = COLORS["item_selected"] if idx == self.selected else COLORS["item"]
            rect = pygame.Rect(SCREEN_W // 2 - 500, 230 + i * (item_h + 10), 1000, item_h)
            pygame.draw.rect(self.screen, color, rect, border_radius=10)
            t = self.font_md.render(ssid, True, COLORS["item_text"])
            self.screen.blit(t, (rect.x + 24, rect.centery - t.get_height() // 2))

    def draw_password(self):
        self.keyboard.draw()

    def draw_connecting(self):
        self.screen.fill(COLORS["bg"])
        self._draw_title()
        s = self.font_md.render(self.status_msg, True, self.status_color)
        self.screen.blit(s, (SCREEN_W // 2 - s.get_width() // 2, SCREEN_H // 2))

    def draw_done(self):
        self.screen.fill(COLORS["bg"])
        self._draw_title()
        s = self.font_lg.render(self.status_msg, True, self.status_color)
        self.screen.blit(s, (SCREEN_W // 2 - s.get_width() // 2, SCREEN_H // 2 - 40))
        hint = self.font_sm.render("Press OK to exit", True, COLORS["subtitle"])
        self.screen.blit(hint, (SCREEN_W // 2 - hint.get_width() // 2, SCREEN_H // 2 + 60))

    def draw_error(self):
        self.screen.fill(COLORS["bg"])
        self._draw_title()
        s = self.font_md.render(self.status_msg, True, self.status_color)
        self.screen.blit(s, (SCREEN_W // 2 - s.get_width() // 2, SCREEN_H // 2))

    def _draw_title(self):
        t = self.font_lg.render("grandmapi  WiFi Setup", True, COLORS["title"])
        self.screen.blit(t, (SCREEN_W // 2 - t.get_width() // 2, 50))

    def handle_event(self, event):
        if event.type == pygame.QUIT:
            return False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_q and (event.mod & pygame.KMOD_CTRL):
                return False

            if self.scene == "list":
                if event.key == pygame.K_UP:
                    self.selected = (self.selected - 1) % len(self.networks)
                elif event.key == pygame.K_DOWN:
                    self.selected = (self.selected + 1) % len(self.networks)
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self.selected_ssid = self.networks[self.selected]
                    self.keyboard = OnScreenKeyboard(self.screen, self.font_md, f"Password for {self.selected_ssid}:")
                    self.scene = "password"

            elif self.scene == "password":
                result = self.keyboard.handle_key(event)
                if result == "CANCEL":
                    self.scene = "list"
                elif result is not None:
                    self._start_connect(self.selected_ssid, result)

            elif self.scene in ("done",):
                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    return False

            elif self.scene == "error":
                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self._start_scan()

        return True

    def run(self):
        running = True
        while running:
            for event in pygame.event.get():
                if not self.handle_event(event):
                    running = False

            if self.scene == "scan":
                self.draw_scan()
            elif self.scene == "list":
                self.draw_list()
            elif self.scene == "password":
                self.draw_password()
            elif self.scene == "connecting":
                self.draw_connecting()
            elif self.scene == "done":
                self.draw_done()
            elif self.scene == "error":
                self.draw_error()

            pygame.display.flip()
            self.clock.tick(30)

        pygame.quit()
        sys.exit(0)


if __name__ == "__main__":
    App().run()
