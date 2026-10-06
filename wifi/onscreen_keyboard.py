import pygame

ROWS = [
    list("1234567890"),
    list("qwertyuiop"),
    list("asdfghjkl"),
    list("zxcvbnm"),
    ["SPACE", "BACKSPACE", "DONE"],
]

COLORS = {
    "bg": (30, 30, 30),
    "key": (60, 60, 60),
    "key_hover": (100, 100, 200),
    "key_text": (255, 255, 255),
    "input_bg": (20, 20, 20),
    "input_text": (255, 255, 255),
    "input_border": (100, 100, 200),
}

KEY_W = 80
KEY_H = 70
KEY_PAD = 8


class OnScreenKeyboard:
    def __init__(self, screen, font, prompt="Enter password:"):
        self.screen = screen
        self.font = font
        self.prompt = prompt
        self.text = ""
        self.row = 0
        self.col = 0
        self.sw, self.sh = screen.get_size()

    def _key_rect(self, row, col):
        row_keys = ROWS[row]
        total_w = sum(self._key_width(k) + KEY_PAD for k in row_keys) - KEY_PAD
        start_x = (self.sw - total_w) // 2
        kb_top = self.sh // 2 - 30

        x = start_x
        for i, key in enumerate(row_keys):
            w = self._key_width(key)
            if i == col:
                y = kb_top + row * (KEY_H + KEY_PAD)
                return pygame.Rect(x, y, w, KEY_H)
            x += w + KEY_PAD
        return None

    def _key_width(self, key):
        if key == "SPACE":
            return KEY_W * 3
        if key in ("BACKSPACE", "DONE"):
            return KEY_W * 2
        return KEY_W

    def draw(self):
        self.screen.fill(COLORS["bg"])

        # Prompt
        prompt_surf = self.font.render(self.prompt, True, (180, 180, 180))
        self.screen.blit(prompt_surf, (self.sw // 2 - prompt_surf.get_width() // 2, 60))

        # Password input box
        box_w = 600
        box_h = 60
        box_x = (self.sw - box_w) // 2
        box_y = 130
        pygame.draw.rect(self.screen, COLORS["input_bg"], (box_x, box_y, box_w, box_h), border_radius=8)
        pygame.draw.rect(self.screen, COLORS["input_border"], (box_x, box_y, box_w, box_h), 2, border_radius=8)
        display_text = "*" * len(self.text)
        text_surf = self.font.render(display_text, True, COLORS["input_text"])
        self.screen.blit(text_surf, (box_x + 16, box_y + (box_h - text_surf.get_height()) // 2))

        # Keys
        for r, row_keys in enumerate(ROWS):
            for c, key in enumerate(row_keys):
                rect = self._key_rect(r, c)
                if rect is None:
                    continue
                color = COLORS["key_hover"] if (r == self.row and c == self.col) else COLORS["key"]
                pygame.draw.rect(self.screen, color, rect, border_radius=6)
                label = "␣" if key == "SPACE" else ("⌫" if key == "BACKSPACE" else key.upper())
                s = self.font.render(label, True, COLORS["key_text"])
                self.screen.blit(s, (rect.centerx - s.get_width() // 2, rect.centery - s.get_height() // 2))

    def handle_key(self, event):
        if event.type != pygame.KEYDOWN:
            return None

        if event.key == pygame.K_UP:
            self.row = (self.row - 1) % len(ROWS)
            self.col = min(self.col, len(ROWS[self.row]) - 1)
        elif event.key == pygame.K_DOWN:
            self.row = (self.row + 1) % len(ROWS)
            self.col = min(self.col, len(ROWS[self.row]) - 1)
        elif event.key == pygame.K_LEFT:
            self.col = (self.col - 1) % len(ROWS[self.row])
        elif event.key == pygame.K_RIGHT:
            self.col = (self.col + 1) % len(ROWS[self.row])
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            key = ROWS[self.row][self.col]
            if key == "BACKSPACE":
                self.text = self.text[:-1]
            elif key == "SPACE":
                self.text += " "
            elif key == "DONE":
                return self.text
            else:
                self.text += key
        elif event.key == pygame.K_ESCAPE:
            return "CANCEL"

        return None
