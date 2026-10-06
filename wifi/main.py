#!/usr/bin/env python3
import sys
import os
import threading
import pygame

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wifi_manager
from onscreen_keyboard import OnScreenKeyboard
from firestick import discovery, adb_manager

COLORS = {
    "bg":             (15, 15, 25),
    "title":          (255, 255, 255),
    "subtitle":       (160, 160, 180),
    "item":           (50, 50, 70),
    "item_selected":  (80, 80, 200),
    "item_text":      (255, 255, 255),
    "status_ok":      (80, 200, 80),
    "status_err":     (200, 80, 80),
    "status_info":    (180, 180, 80),
    "menu_item":      (40, 40, 60),
    "menu_selected":  (80, 80, 200),
    "danger":         (200, 80, 80),
    "danger_sel":     (255, 60, 60),
}

SCREEN_W, SCREEN_H = 1920, 1080


class App:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.FULLSCREEN)
        pygame.display.set_caption("grandmapi")
        self.clock = pygame.time.Clock()
        self.font_lg = pygame.font.SysFont("dejavusans", 52)
        self.font_md = pygame.font.SysFont("dejavusans", 38)
        self.font_sm = pygame.font.SysFont("dejavusans", 30)

        # WiFi state
        self.wifi_scene = "scan"
        self.networks = []
        self.wifi_selected = 0
        self.selected_ssid = ""
        self.keyboard = None
        self.status_msg = ""
        self.status_color = COLORS["status_info"]

        # Main menu state
        self.menu_items = ["Fire Stick", "WiFi Settings"]
        self.menu_selected = 0

        # Fire Stick state
        self.fs_scene = "scan"   # scan | list | control | screenshot
        self.fs_devices = []
        self.fs_selected = 0
        self.fs_connected_ip = None
        self.fs_device_name = ""
        self.fs_scan_progress = 0
        self.fs_scan_total = 254
        self.fs_status = ""
        self.fs_status_color = COLORS["status_info"]
        self.fs_screenshot_path = None
        self.fs_screenshot_surface = None
        self.control_items = ["Take Screenshot", "Open YouTube", "Lock to YouTube", "Unlock", "Back"]
        self.control_selected = 0

        # Top-level scene
        self.scene = None
        self._check_wifi_on_boot()

    # ------------------------------------------------------------------ boot
    def _check_wifi_on_boot(self):
        self.scene = "wifi_scan"
        self.status_msg = "Checking WiFi..."
        self.status_color = COLORS["status_info"]

        def check():
            if wifi_manager.is_connected():
                self.scene = "menu"
            elif wifi_manager.load_saved_networks():
                self.status_msg = "Connecting to saved network..."
                if wifi_manager.auto_connect():
                    self.scene = "menu"
                else:
                    self.status_msg = "Saved networks not available. Scanning..."
                    self._start_wifi_scan()
            else:
                self._start_wifi_scan()

        threading.Thread(target=check, daemon=True).start()

    # ------------------------------------------------------------------ WiFi
    def _start_wifi_scan(self):
        self.scene = "wifi_scan"
        self.status_msg = "Scanning for networks..."
        self.status_color = COLORS["status_info"]

        def scan():
            nets = wifi_manager.scan_networks()
            self.networks = nets if nets else []
            self.wifi_selected = 0
            if self.networks:
                self.scene = "wifi_list"
            else:
                self.status_msg = "No networks found. Press OK to retry."
                self.status_color = COLORS["status_err"]
                self.scene = "wifi_error"

        threading.Thread(target=scan, daemon=True).start()

    def _start_connect(self, ssid, password):
        self.scene = "wifi_connecting"
        self.status_msg = f"Connecting to {ssid}..."
        self.status_color = COLORS["status_info"]

        def connect():
            ok = wifi_manager.connect(ssid, password)
            if ok:
                self.status_msg = f"Connected to {ssid}!"
                self.status_color = COLORS["status_ok"]
                self.scene = "wifi_done"
            else:
                self.status_msg = "Connection failed. Press OK to retry."
                self.status_color = COLORS["status_err"]
                self.scene = "wifi_error"

        threading.Thread(target=connect, daemon=True).start()

    # ---------------------------------------------------------------- Fire Stick
    def _start_fs_scan(self):
        self.fs_scene = "scan"
        self.fs_devices = []
        self.fs_scan_progress = 0
        self.fs_status = "Scanning network for Fire Sticks..."
        self.fs_status_color = COLORS["status_info"]

        def on_progress(done, total):
            self.fs_scan_progress = done
            self.fs_scan_total = total

        def scan():
            devices = discovery.scan(on_progress=on_progress)
            self.fs_devices = devices
            self.fs_selected = 0
            if devices:
                self.fs_scene = "list"
                self.fs_status = f"Found {len(devices)} device(s)."
            else:
                self.fs_status = "No Fire Sticks found. Press OK to retry."
                self.fs_status_color = COLORS["status_err"]
                self.fs_scene = "error"

        threading.Thread(target=scan, daemon=True).start()

    def _connect_firestick(self, ip):
        self.fs_scene = "connecting"
        self.fs_status = f"Connecting to {ip}..."
        self.fs_status_color = COLORS["status_info"]

        def connect():
            ok = adb_manager.connect(ip)
            if ok:
                self.fs_connected_ip = ip
                self.fs_device_name = adb_manager.get_device_name(ip)
                self.control_selected = 0
                self.fs_scene = "control"
                self.fs_status = f"Connected to {self.fs_device_name}"
                self.fs_status_color = COLORS["status_ok"]
            else:
                self.fs_status = "Could not connect. Is ADB enabled on the Fire Stick?"
                self.fs_status_color = COLORS["status_err"]
                self.fs_scene = "error"

        threading.Thread(target=connect, daemon=True).start()

    def _run_control_action(self, action):
        ip = self.fs_connected_ip

        def run():
            if action == "Take Screenshot":
                self.fs_status = "Taking screenshot..."
                path = adb_manager.screenshot(ip)
                if path:
                    img = pygame.image.load(path)
                    self.fs_screenshot_surface = pygame.transform.scale(img, (SCREEN_W, SCREEN_H))
                    self.fs_scene = "screenshot"
                else:
                    self.fs_status = "Screenshot failed."
                    self.fs_status_color = COLORS["status_err"]
            elif action == "Open YouTube":
                self.fs_status = "Opening YouTube..."
                adb_manager.open_youtube(ip)
                self.fs_status = "YouTube opened."
                self.fs_status_color = COLORS["status_ok"]
            elif action == "Lock to YouTube":
                self.fs_status = "Locking to YouTube..."
                adb_manager.lock_to_youtube(ip)
                self.fs_status = "Locked to YouTube."
                self.fs_status_color = COLORS["status_ok"]
            elif action == "Unlock":
                self.fs_status = "Unlocking..."
                adb_manager.unlock(ip)
                self.fs_status = "Unlocked."
                self.fs_status_color = COLORS["status_ok"]

        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------ draw
    def _draw_title(self, subtitle=""):
        t = self.font_lg.render("grandmapi", True, COLORS["title"])
        self.screen.blit(t, (SCREEN_W // 2 - t.get_width() // 2, 40))
        if subtitle:
            s = self.font_sm.render(subtitle, True, COLORS["subtitle"])
            self.screen.blit(s, (SCREEN_W // 2 - s.get_width() // 2, 110))

    def _draw_status(self, msg, color, y=None):
        y = y or SCREEN_H // 2
        s = self.font_md.render(msg, True, color)
        self.screen.blit(s, (SCREEN_W // 2 - s.get_width() // 2, y))

    def _draw_menu_list(self, items, selected, top_y=220, item_h=90, color_sel=None, color_norm=None):
        color_sel = color_sel or COLORS["item_selected"]
        color_norm = color_norm or COLORS["item"]
        for i, label in enumerate(items):
            color = color_sel if i == selected else color_norm
            rect = pygame.Rect(SCREEN_W // 2 - 500, top_y + i * (item_h + 12), 1000, item_h)
            pygame.draw.rect(self.screen, color, rect, border_radius=12)
            t = self.font_md.render(label, True, COLORS["item_text"])
            self.screen.blit(t, (rect.x + 24, rect.centery - t.get_height() // 2))

    def draw(self):
        self.screen.fill(COLORS["bg"])

        if self.scene == "menu":
            self._draw_title("What would you like to do?")
            hint = self.font_sm.render("↑↓ Navigate   OK Select", True, COLORS["subtitle"])
            self.screen.blit(hint, (SCREEN_W // 2 - hint.get_width() // 2, 160))
            self._draw_menu_list(self.menu_items, self.menu_selected)

        elif self.scene == "wifi_scan":
            self._draw_title("WiFi Setup")
            self._draw_status(self.status_msg, self.status_color)

        elif self.scene == "wifi_list":
            self._draw_title("WiFi Setup", "Select your network")
            hint = self.font_sm.render(
                f"↑↓ Navigate   OK Select   Connected: {wifi_manager.current_ssid() or 'None'}",
                True, COLORS["subtitle"]
            )
            self.screen.blit(hint, (SCREEN_W // 2 - hint.get_width() // 2, 155))
            item_h = 80
            visible = 8
            start = max(0, self.wifi_selected - visible // 2)
            for i, idx in enumerate(range(start, min(len(self.networks), start + visible))):
                color = COLORS["item_selected"] if idx == self.wifi_selected else COLORS["item"]
                rect = pygame.Rect(SCREEN_W // 2 - 500, 220 + i * (item_h + 10), 1000, item_h)
                pygame.draw.rect(self.screen, color, rect, border_radius=10)
                t = self.font_md.render(self.networks[idx], True, COLORS["item_text"])
                self.screen.blit(t, (rect.x + 24, rect.centery - t.get_height() // 2))

        elif self.scene in ("wifi_connecting", "wifi_done", "wifi_error"):
            self._draw_title("WiFi Setup")
            self._draw_status(self.status_msg, self.status_color)
            if self.scene in ("wifi_done", "wifi_error"):
                hint = self.font_sm.render("Press OK to continue", True, COLORS["subtitle"])
                self.screen.blit(hint, (SCREEN_W // 2 - hint.get_width() // 2, SCREEN_H // 2 + 70))

        elif self.scene == "wifi_password":
            self.keyboard.draw()

        elif self.scene == "firestick":
            if self.fs_scene == "scan":
                self._draw_title("Fire Stick", "Scanning network...")
                bar_w = 800
                bar_h = 30
                bar_x = (SCREEN_W - bar_w) // 2
                bar_y = SCREEN_H // 2
                pygame.draw.rect(self.screen, COLORS["item"], (bar_x, bar_y, bar_w, bar_h), border_radius=6)
                progress = self.fs_scan_progress / max(self.fs_scan_total, 1)
                pygame.draw.rect(self.screen, COLORS["item_selected"],
                                 (bar_x, bar_y, int(bar_w * progress), bar_h), border_radius=6)
                pct = self.font_sm.render(f"{int(progress * 100)}%", True, COLORS["subtitle"])
                self.screen.blit(pct, (SCREEN_W // 2 - pct.get_width() // 2, bar_y + 40))

            elif self.fs_scene == "list":
                self._draw_title("Fire Stick", "Select a device")
                hint = self.font_sm.render("↑↓ Navigate   OK Connect   Esc Back", True, COLORS["subtitle"])
                self.screen.blit(hint, (SCREEN_W // 2 - hint.get_width() // 2, 155))
                self._draw_menu_list(self.fs_devices, self.fs_selected)

            elif self.fs_scene == "connecting":
                self._draw_title("Fire Stick")
                self._draw_status(self.fs_status, self.fs_status_color)

            elif self.fs_scene == "control":
                self._draw_title("Fire Stick", self.fs_device_name)
                status = self.font_sm.render(self.fs_status, True, self.fs_status_color)
                self.screen.blit(status, (SCREEN_W // 2 - status.get_width() // 2, 155))
                hint = self.font_sm.render("↑↓ Navigate   OK Select   Esc Back", True, COLORS["subtitle"])
                self.screen.blit(hint, (SCREEN_W // 2 - hint.get_width() // 2, 185))
                for i, label in enumerate(self.control_items):
                    is_sel = i == self.control_selected
                    if label in ("Lock to YouTube", "Unlock"):
                        color = COLORS["danger_sel"] if is_sel else COLORS["danger"]
                    else:
                        color = COLORS["item_selected"] if is_sel else COLORS["item"]
                    rect = pygame.Rect(SCREEN_W // 2 - 400, 250 + i * 100, 800, 80)
                    pygame.draw.rect(self.screen, color, rect, border_radius=12)
                    t = self.font_md.render(label, True, COLORS["item_text"])
                    self.screen.blit(t, (rect.x + 24, rect.centery - t.get_height() // 2))

            elif self.fs_scene == "screenshot":
                if self.fs_screenshot_surface:
                    self.screen.blit(self.fs_screenshot_surface, (0, 0))
                    hint = self.font_sm.render("Press any key to go back", True, (255, 255, 255))
                    self.screen.blit(hint, (20, 20))

            elif self.fs_scene == "error":
                self._draw_title("Fire Stick")
                self._draw_status(self.fs_status, self.fs_status_color)
                hint = self.font_sm.render("Press OK to retry   Esc Back", True, COLORS["subtitle"])
                self.screen.blit(hint, (SCREEN_W // 2 - hint.get_width() // 2, SCREEN_H // 2 + 70))

    # ----------------------------------------------------------------- events
    def handle_event(self, event):
        if event.type == pygame.QUIT:
            return False
        if event.type != pygame.KEYDOWN:
            return True

        key = event.key
        enter = key in (pygame.K_RETURN, pygame.K_KP_ENTER)
        esc = key == pygame.K_ESCAPE
        up = key == pygame.K_UP
        down = key == pygame.K_DOWN
        quit_app = key == pygame.K_q and (event.mod & pygame.KMOD_CTRL)

        if quit_app:
            return False

        # Main menu
        if self.scene == "menu":
            if up:
                self.menu_selected = (self.menu_selected - 1) % len(self.menu_items)
            elif down:
                self.menu_selected = (self.menu_selected + 1) % len(self.menu_items)
            elif enter:
                choice = self.menu_items[self.menu_selected]
                if choice == "WiFi Settings":
                    self._start_wifi_scan()
                elif choice == "Fire Stick":
                    self.scene = "firestick"
                    self._start_fs_scan()

        # WiFi scenes
        elif self.scene == "wifi_list":
            if up:
                self.wifi_selected = (self.wifi_selected - 1) % len(self.networks)
            elif down:
                self.wifi_selected = (self.wifi_selected + 1) % len(self.networks)
            elif enter:
                self.selected_ssid = self.networks[self.wifi_selected]
                self.keyboard = OnScreenKeyboard(
                    self.screen, self.font_md, f"Password for {self.selected_ssid}:"
                )
                self.scene = "wifi_password"
            elif esc:
                self.scene = "menu"

        elif self.scene == "wifi_password":
            result = self.keyboard.handle_key(event)
            if result == "CANCEL":
                self.scene = "wifi_list"
            elif result is not None:
                self._start_connect(self.selected_ssid, result)

        elif self.scene == "wifi_done":
            if enter:
                self.scene = "menu"

        elif self.scene == "wifi_error":
            if enter:
                self._start_wifi_scan()
            elif esc:
                self.scene = "menu"

        # Fire Stick scenes
        elif self.scene == "firestick":
            if self.fs_scene == "list":
                if up:
                    self.fs_selected = (self.fs_selected - 1) % len(self.fs_devices)
                elif down:
                    self.fs_selected = (self.fs_selected + 1) % len(self.fs_devices)
                elif enter:
                    self._connect_firestick(self.fs_devices[self.fs_selected])
                elif esc:
                    self.scene = "menu"

            elif self.fs_scene == "control":
                if up:
                    self.control_selected = (self.control_selected - 1) % len(self.control_items)
                elif down:
                    self.control_selected = (self.control_selected + 1) % len(self.control_items)
                elif enter:
                    action = self.control_items[self.control_selected]
                    if action == "Back":
                        self.fs_scene = "list"
                    else:
                        self._run_control_action(action)
                elif esc:
                    self.fs_scene = "list"

            elif self.fs_scene == "screenshot":
                self.fs_scene = "control"

            elif self.fs_scene == "error":
                if enter:
                    self._start_fs_scan()
                elif esc:
                    self.scene = "menu"

        return True

    def run(self):
        running = True
        while running:
            for event in pygame.event.get():
                if not self.handle_event(event):
                    running = False
            self.draw()
            pygame.display.flip()
            self.clock.tick(30)
        pygame.quit()
        sys.exit(0)


if __name__ == "__main__":
    App().run()
