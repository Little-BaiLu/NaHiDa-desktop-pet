#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
目光跟随鼠标的桌面宠物（Windows / tkinter + Pillow）

特性
  * 无边框、透明、置顶的小窗口
  * 眼睛（瞳孔层）实时跟随鼠标光标
  * 头部微倾斜、待机随机行为、眨眼、拖拽、抛掷掉落、点击冒爱心
  * 素材完全外部化在 assets/ 里，换成纳西妲立绘即可（见 README.md）

用法:
    python pet.py                 # 使用同目录下的 pet.json
    python pet.py 其它配置.json
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
import time
import tkinter as tk
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageTk
except Exception as exc:  # pragma: no cover
    sys.stderr.write(
        "需要 Pillow：请先运行  pip install pillow\n原始错误: %s\n" % exc
    )
    raise SystemExit(2)

HERE = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

DEFAULTS: Dict = {
    "name": "Nahida",
    "sprite_dir": "assets/namecard_placeholder",
    "body": "body.png",
    "eyes": ["eye_l.png", "eye_r.png"],
    # 眼睛在 body.png 坐标系里的位置，以及眼睛可见框大小（像素）
    "eye_layout": [
        {"x": 92, "y": 128, "w": 46, "h": 46},
        {"x": 168, "y": 128, "w": 46, "h": 46},
    ],
    "anchor": [0.5, 0.08],          # 视线起点：相对窗口宽/高（额头附近）
    "scale": 1.0,                   # 整体缩放
    "fps": 30,
    "max_pupil_offset": 7.0,        # 瞳孔相对眼窝最多偏移多少像素（未缩放前）
    "gaze_gain": 1.25,              # 视线的“夸张”程度
    "head_tilt_max_deg": 6.0,       # 头部左右微倾最大角度
    "head_tilt_gain": 1500.0,       # 鼠标水平距离 -> 倾斜角度 的灵敏度
    "blink": {"min_interval": 2.4, "max_interval": 6.0, "duration": 0.16},
    "behavior": {"min_interval": 1.6, "max_interval": 4.5},
    "look_around": {"max_offset": 8.0, "duration": 0.9},
    "hop": {"height": 34.0, "duration": 0.55},
    "gravity": 2600.0,              # 抛掷后的重力（像素/秒^2）
    "bounce": 0.42,
    "always_on_top": True,
    "click_through": False,
    "start_position": None,         # [x, y] 或 null=右下角
}

COLOR_KEY = "#010203"  # 透明色键（不要用在素材里）
BG = "#202124"


def load_config(path: str) -> Dict:
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            user = json.load(fh)
        for key, value in user.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    return cfg


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


# 注意（踩过的坑）：不要在这里调用 SetProcessDpiAwareness / SetProcessDPIAware。
# 那样会让 Tk 的坐标系劈叉——GetWindowRect 按逻辑像素、画布却按物理像素，
# 结果是窗口只有画布的四分之一大，桌宠被裁掉大半还跑到屏幕外。
# 缩放交给系统 DPI 虚拟化处理就好：想要更大就调 pet.json 里的 scale。


def ease_out(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def ease_in_out(t: float) -> float:
    return 3 * t * t - 2 * t * t * t


# --------------------------------------------------------------------------
# 动画 / 行为状态
# --------------------------------------------------------------------------


@dataclass
class Motion:
    kind: str = "idle"          # idle | look | hop | pop
    t: float = 0.0
    dur: float = 1.0
    data: Dict = field(default_factory=dict)


class Pet:
    def __init__(self, cfg: Dict) -> None:
        self.cfg = cfg
        self.scale = float(cfg["scale"])
        self.sprite_dir = os.path.join(HERE, cfg["sprite_dir"])

        body_img = self._load(os.path.join(self.sprite_dir, cfg["body"]))
        self.base_w, self.base_h = body_img.size
        self.eyes_src = [
            self._load(os.path.join(self.sprite_dir, name)) for name in cfg["eyes"]
        ]
        # 眼睛素材按配置的可见框缩放到统一大小（未套用整体 scale）
        self.eye_layout = [dict(item) for item in cfg["eye_layout"]]
        self.eyes_scaled: List[Image.Image] = []
        for img, layout in zip(self.eyes_src, self.eye_layout):
            self.eyes_scaled.append(
                img.resize((int(layout["w"]), int(layout["h"])), Image.LANCZOS)
            )
        self.body_src = body_img

        self.win_w = max(1, int(round(self.base_w * self.scale)))
        self.win_h = max(1, int(round(self.base_h * self.scale)))

        # 状态
        self.offset = [0.0, 0.0]        # 瞳孔偏移（像素，未缩放）
        self.blink = 0.0                # 0=睁, 1=闭
        self.tilt = 0.0                 # 头部倾斜角度
        self.hop_z = 0.0                # 跳跃高度（像素）
        self.pos_x = 0.0
        self.pos_y = 0.0
        self.vel_y = 0.0
        self.airborne = False
        self.gaze_enabled = True
        self.dragging = False
        self.emote: Optional[str] = None
        self.emote_t = 0.0

        self.motion = Motion(dur=random.uniform(
            cfg["behavior"]["min_interval"], cfg["behavior"]["max_interval"]))
        self.next_blink = time.time() + random.uniform(
            cfg["blink"]["min_interval"], cfg["blink"]["max_interval"])
        self.blink_until = 0.0
        self.target_offset = [0.0, 0.0]
        self.eye_pos = [0.0, 0.0]       # 平滑后的视线偏移

        self._build_window()

    # ---------------- 素材 ----------------

    @staticmethod
    def _load(path: str) -> Image.Image:
        if not os.path.exists(path):
            raise SystemExit("找不到素材文件: %s" % path)
        return Image.open(path).convert("RGBA")

    # ---------------- 窗口 ----------------

    def _build_window(self) -> None:
        self.root = tk.Tk()
        self.root.title(cfg_name(self.cfg))
        self.root.overrideredirect(True)
        try:
            self.root.attributes("-transparentcolor", COLOR_KEY)
        except tk.TclError:
            pass  # 非 Windows / 不支持时退化为不透明底
        self.root.configure(bg=COLOR_KEY)

        # 屏幕尺寸和起始位置必须先算出来，再声明窗口几何。
        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()
        start = self.cfg.get("start_position")
        if start:
            self.pos_x, self.pos_y = float(start[0]), float(start[1])
        else:
            # 默认落在屏幕中下偏右：这个位置最容易被一眼看到，
            # 不会像贴死屏幕右下角那样被任务栏/托盘区吞掉。
            self.pos_x = self.screen_w - self.win_w - 220.0
            self.pos_y = self.screen_h - self.win_h - 190.0

        # 尺寸必须在这里显式声明：overrideredirect 之后窗口不会自动跟着
        # 画布的请求尺寸走，否则会停在 Tk 默认的 200x200，里面什么都看不到。
        self.root.geometry("%dx%d+%d+%d" % (
            self.win_w, self.win_h, int(self.pos_x), int(self.pos_y)))

        self.canvas = tk.Canvas(
            self.root, width=self.win_w, height=self.win_h,
            bg=COLOR_KEY, highlightthickness=0, bd=0,
        )
        # 用 place 而不是 pack：pack 要等事件循环跑起来才真正布局，
        # 那之前 canvas 一直是 1x1，窗口里会是空的。
        self.canvas.place(x=0, y=0, width=self.win_w, height=self.win_h)

        # 无边框窗口默认不抢焦点，也不一定浮在最上层；
        # 启动时主动抬升 + 抢焦点 + 置顶刷新，确保用户看得见。
        self.root.update()
        self.root.lift()
        self._raise_again()
        try:
            self.root.bell()
        except tk.TclError:
            pass

        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_menu)
        self.canvas.bind("<Double-Button-1>", lambda e: self.trigger_emote("heart"))

        self._make_menu()
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._img_id = self.canvas.create_image(0, 0, anchor="nw")
        self._last = time.time()
        self.root.after(10, self._schedule_behavior)

    def _raise_again(self) -> None:
        """把窗口抬到最上层。个别环境下启动瞬间的 lift 不生效，
        所以在启动后 0.3 秒再补一次。"""
        try:
            self.root.attributes("-topmost", bool(self.cfg["always_on_top"]))
            self.root.lift()
            if self.cfg["always_on_top"]:
                self.root.focus_force()
        except tk.TclError:
            pass
        self.root.after(300, self._raise_again2)

    def _raise_again2(self) -> None:
        try:
            self.root.update_idletasks()
            if self.cfg["always_on_top"]:
                self.root.attributes("-topmost", False)
                self.root.attributes("-topmost", True)
            self.root.lift()
        except tk.TclError:
            pass

    def _make_menu(self) -> None:
        bg, fg = BG, "#e8eaed"
        self.menu = tk.Menu(self.root, tearoff=0, bg=bg, fg=fg,
                            activebackground="#3c4043", activeforeground="#ffffff")
        self.menu.add_command(label="打声招呼 (♥)", command=lambda: self.trigger_emote("heart"))
        self.var_gaze = tk.BooleanVar(value=self.gaze_enabled)
        self.menu.add_checkbutton(label="目光跟随鼠标", variable=self.var_gaze,
                                  command=self._toggle_gaze)
        self.var_top = tk.BooleanVar(value=bool(self.cfg["always_on_top"]))
        self.menu.add_checkbutton(label="总在最前", variable=self.var_top,
                                  command=self._toggle_top)
        self.menu.add_separator()
        self.menu.add_command(label="放大 10%", command=lambda: self._zoom(1.1))
        self.menu.add_command(label="缩小 10%", command=lambda: self._zoom(1 / 1.1))
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.quit)

    # ---------------- 输入 ----------------

    def _on_press(self, event: "tk.Event") -> None:
        self._press_xy = (event.x_root, event.y_root)
        self._press_win = (self.pos_x, self.pos_y)
        self._moved = False

    def _on_drag(self, event: "tk.Event") -> None:
        px, py = self._press_xy
        ox, oy = self._press_win
        dx, dy = event.x_root - px, event.y_root - py
        if abs(dx) + abs(dy) > 3:
            self._moved = True
            self.dragging = True
        self.pos_x, self.pos_y = ox + dx, oy + dy
        self.vel_y = 0.0
        self.airborne = False
        self._place()

    def _on_release(self, event: "tk.Event") -> None:
        self.dragging = False
        if not getattr(self, "_moved", False):
            self.trigger_emote("heart")
            return
        # 抛掷：用最近几帧位移估算速度（简化：取一次小幅下落）
        self.airborne = True
        self.vel_y = 0.0

    def _on_menu(self, event: "tk.Event") -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def _toggle_gaze(self) -> None:
        self.gaze_enabled = bool(self.var_gaze.get())
        if not self.gaze_enabled:
            self.target_offset = [0.0, 0.0]

    def _toggle_top(self) -> None:
        self.root.attributes("-topmost", bool(self.var_top.get()))

    def _zoom(self, factor: float) -> None:
        self.scale = clamp(self.scale * factor, 0.25, 4.0)
        self.win_w = max(1, int(round(self.base_w * self.scale)))
        self.win_h = max(1, int(round(self.base_h * self.scale)))
        self.canvas.configure(width=self.win_w, height=self.win_h)
        self._place()

    # ---------------- 行为 ----------------

    def _schedule_behavior(self) -> None:
        b = self.cfg["behavior"]
        self.motion = Motion(
            kind=random.choice(["idle", "idle", "idle", "look", "look", "hop"]),
            dur=random.uniform(b["min_interval"], b["max_interval"]),
            data=random.choice([{}, {}, {"kind": None}, {"kind": "flower"}]),
        )
        if self.motion.kind == "look":
            m = self.cfg["look_around"]
            ang = random.uniform(0, math.tau)
            r = random.uniform(0.35, 1.0) * m["max_offset"]
            self.motion.data = {"dx": math.cos(ang) * r, "dy": math.sin(ang) * r * 0.7}
            self.motion.dur = m["duration"]
        elif self.motion.kind == "hop":
            h = self.cfg["hop"]
            self.motion.dur = h["duration"]
            self.motion.data = {"height": h["height"] * random.uniform(0.7, 1.2)}

    def trigger_emote(self, name: str) -> None:
        self.emote = name
        self.emote_t = 0.0

    # ---------------- 主循环 ----------------

    def _place(self) -> None:
        self.root.geometry("+%d+%d" % (int(self.pos_x), int(self.pos_y)))

    def _update(self, dt: float) -> None:
        now = time.time()
        cfg = self.cfg

        # --- 行为计时 ---
        self.motion.t += dt
        if self.motion.t >= self.motion.dur:
            self._schedule_behavior()

        # --- 视线目标 ---
        gaze = [0.0, 0.0]
        if self.gaze_enabled and not self.dragging:
            mx, my = self.root.winfo_pointerxy()
            ax = self.pos_x + self.win_w * cfg["anchor"][0]
            ay = self.pos_y + self.win_h * cfg["anchor"][1]
            vx, vy = mx - ax, my - ay
            dist = math.hypot(vx, vy)
            if dist > 1e-3:
                unit = (vx / dist, vy / dist)
                # 距离越远越贴近最大偏移
                mag = min(1.0, dist / 420.0) * cfg["max_pupil_offset"]
                gain = cfg["gaze_gain"]
                gaze = [clamp(unit[0] * mag * gain, -cfg["max_pupil_offset"], cfg["max_pupil_offset"]),
                        clamp(unit[1] * mag * 0.75 * gain, -cfg["max_pupil_offset"] * 0.8,
                              cfg["max_pupil_offset"] * 0.8)]
        if self.motion.kind == "look":
            p = self.motion.t / max(self.motion.dur, 1e-6)
            k = math.sin(min(1.0, p) * math.pi)
            gaze = [gaze[0] + self.motion.data.get("dx", 0.0) * k,
                    gaze[1] + self.motion.data.get("dy", 0.0) * k]
        # 平滑趋近，眼神有惯性
        lerp = min(1.0, dt * 12.0)
        self.eye_pos[0] += (gaze[0] - self.eye_pos[0]) * lerp
        self.eye_pos[1] += (gaze[1] - self.eye_pos[1]) * lerp
        self.offset = [self.eye_pos[0] * self.scale, self.eye_pos[1] * self.scale]

        # --- 头部倾斜 ---
        want_tilt = -self.offset[0] / max(cfg["max_pupil_offset"] * self.scale, 1e-6) \
            * cfg["head_tilt_max_deg"] * clamp(cfg["gaze_gain"] / 1.5, 0.0, 2.0)
        want_tilt = clamp(want_tilt, -cfg["head_tilt_max_deg"], cfg["head_tilt_max_deg"])
        self.tilt += (want_tilt - self.tilt) * min(1.0, dt * 9.0)

        # --- 眨眼 ---
        bcfg = cfg["blink"]
        if now >= self.next_blink and self.blink_until == 0.0:
            self.blink_until = now + bcfg["duration"]
            self.next_blink = now + random.uniform(bcfg["min_interval"], bcfg["max_interval"])
        if self.blink_until:
            p = 1.0 - (self.blink_until - now) / bcfg["duration"]
            self.blink = math.sin(clamp(p, 0.0, 1.0) * math.pi)
            if now >= self.blink_until:
                self.blink_until = 0.0
                self.blink = 0.0

        # --- 跳跃 / 掉落 ---
        if self.motion.kind == "hop" and self.motion.t <= self.motion.dur:
            p = self.motion.t / self.motion.dur
            self.hop_z = math.sin(p * math.pi) * self.motion.data.get("height", 30.0)
        else:
            self.hop_z *= max(0.0, 1.0 - dt * 10.0)

        if self.airborne and not self.dragging:
            self.vel_y += cfg["gravity"] * dt
            self.pos_y += self.vel_y * dt
            floor = self.screen_h - self.win_h - 8
            if self.pos_y >= floor:
                self.pos_y = floor
                if abs(self.vel_y) > 260:
                    self.vel_y = -self.vel_y * cfg["bounce"]
                else:
                    self.vel_y = 0.0
                    self.airborne = False

        # 出屏保护
        self.pos_x = clamp(self.pos_x, -self.win_w * 0.3, self.screen_w - self.win_w * 0.7)
        if not self.airborne:
            self.pos_y = clamp(self.pos_y, 0, self.screen_h - self.win_h * 0.5)
        self._place()

        # --- 情绪计时 ---
        if self.emote:
            self.emote_t += dt
            if self.emote_t > 1.3:
                self.emote = None

        self._render(y_shift=-self.hop_z)

    # ---------------- 渲染 ----------------

    def _render(self, y_shift: float = 0.0) -> None:
        frame = Image.new("RGBA", (self.win_w, self.win_h), (0, 0, 0, 0))
        body = self.body_src
        if abs(self.scale - 1.0) > 1e-6:
            body = body.resize((self.win_w, self.win_h), Image.LANCZOS)
        if abs(self.tilt) > 0.05:
            body = body.rotate(-self.tilt, resample=Image.BICUBIC, expand=False,
                               center=(self.win_w / 2, self.win_h * 0.45))
        frame.alpha_composite(body)
        parts = frame

        for idx, layout in enumerate(self.eye_layout):
            src = self.eyes_scaled[idx]
            w = max(1, int(round(layout["w"] * self.scale)))
            h = max(1, int(round(layout["h"] * self.scale)))
            eye = src.resize((w, h), Image.LANCZOS)
            if self.blink > 0.02:
                # 压到 28% 高度就够了；压得太扁会变成"没眼睛"
                squashed = max(1, int(round(h * (1.0 - 0.72 * self.blink))))
                eye = eye.resize((w, squashed), Image.LANCZOS)
            dx = int(round(layout["x"] * self.scale + self.offset[0]))
            dy = int(round(layout["y"] * self.scale + self.offset[1] + h - eye.size[1]))
            parts.alpha_composite(eye, (dx, dy))

        if abs(self.tilt) > 0.05:
            parts = parts.rotate(-self.tilt, resample=Image.BICUBIC,
                                 center=(self.win_w / 2, self.win_h * 0.45))

        out = Image.new("RGBA", (self.win_w, self.win_h), (0, 0, 0, 0))
        out.alpha_composite(parts, (0, int(round(y_shift))))
        if self.emote:
            out.alpha_composite(self._emote_sprite(), self._emote_pos())
        frame = out

        rgb = frame.convert("RGB")
        key = tuple(int(COLOR_KEY[i:i + 2], 16) for i in (1, 3, 5))
        keyed = Image.new("RGB", frame.size, key)
        # 完全透明的像素直接留色键，其余按 alpha 软边贴合：
        # 这样既不会有灰描边，也不会出现突兀的硬切口。
        keyed.paste(rgb, (0, 0), frame.split()[3])
        hard = frame.split()[3].point(lambda a: 255 if a > 200 else 0)
        keyed.paste(Image.new("RGB", frame.size, key), (0, 0),
                    Image.eval(hard, lambda v: 255 - v))
        self._photo = ImageTk.PhotoImage(keyed)
        self.canvas.itemconfigure(self._img_id, image=self._photo)

    def _emote_sprite(self) -> Image.Image:
        s = max(12, int(22 * self.scale))
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        col = (255, 110, 150, 255) if self.emote == "heart" else (150, 235, 120, 255)
        if self.emote == "heart":
            d.ellipse([2, 3, s * 0.52, s * 0.62], fill=col)
            d.ellipse([s * 0.48, 3, s - 2, s * 0.62], fill=col)
            d.polygon([(2, s * 0.45), (s - 2, s * 0.45), (s / 2, s - 2)], fill=col)
        else:
            cx, cy, r = s / 2, s / 2, s * 0.46
            d.ellipse([cx - r * 0.28, cy - r, cx + r * 0.28, cy + r], fill=col)
            d.ellipse([cx - r, cy - r * 0.28, cx + r, cy + r * 0.28], fill=col)
        return img

    def _emote_pos(self) -> Tuple[int, int]:
        rise = int(self.emote_t * 34)
        return (int(self.win_w * 0.68), int(self.win_h * 0.06) - rise)

    # ---------------- 生命周期 ----------------

    def _tick(self) -> None:
        now = time.time()
        dt = min(0.05, now - self._last)
        self._last = now
        self._update(dt)
        self.root.after(max(1, int(1000 / self.cfg["fps"])), self._tick)

    def run(self) -> None:
        self._tick()
        self.root.mainloop()
    def selftest(self, frames: int = 90, out: str = "selftest_frame.png") -> None:
        """不开事件循环跑若干帧，用于自检渲染管线是否正常。"""
        for i in range(frames):
            self._last = time.time() - 1.0 / self.cfg["fps"]
            self._update(1.0 / self.cfg["fps"])
            self.root.update_idletasks()
        self._render(y_shift=-self.hop_z)
        rgb = Image.new("RGB", (self.win_w, self.win_h), tuple(
            int(COLOR_KEY[i:i + 2], 16) for i in (1, 3, 5)))
        if self._photo is not None:
            rgb.paste(ImageTk.getimage(self._photo), (0, 0))
        rgb.save(out)
        print("selftest ok: %s  (%dx%d)" % (out, self.win_w, self.win_h))

    def quit(self) -> None:
        self.root.destroy()


def cfg_name(cfg: Dict) -> str:
    return "%s · 桌宠" % cfg.get("name", "DesktopPet")


def report_fatal(exc: BaseException, debug: bool) -> None:
    """启动/运行失败时，把原因写到日志（和控制台），并尽量弹窗告知。"""
    import traceback

    tb = traceback.format_exc()
    log_path = os.path.join(HERE, "pet_crash.log")
    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(tb)
    except OSError:
        log_path = "(写日志失败)"
    sys.stderr.write(tb)
    sys.stderr.write("\n日志: %s\n" % log_path)
    if not debug:
        sys.stderr.write("提示: 用 start_pet_debug.bat 启动可以看到这份信息。\n")
    try:
        import tkinter.messagebox as mb

        root = tk.Tk()
        root.withdraw()
        mb.showerror("桌宠启动失败", "%s: %s\n\n详细日志:\n%s" % (
            type(exc).__name__, exc, log_path))
        root.destroy()
    except Exception:
        pass


def install_crash_logger() -> str:
    """把 Tk 事件循环里未捕获的异常也写进日志，否则窗口出错只会静默消失。"""
    import traceback

    path = os.path.join(HERE, "pet_crash.log")

    def hook(exc_type, exc_value, exc_tb) -> None:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n--- %s ---\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            traceback.print_exception(exc_type, exc_value, exc_tb, file=fh)
        sys.stderr.write("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))

    sys.excepthook = hook
    return path


def main() -> None:
    debug = "--debug" in sys.argv
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    cfg_path = argv[0] if argv else os.path.join(HERE, "pet.json")
    install_crash_logger()

    if debug:
        print("Python : %s" % sys.version.replace("\n", " "))
        print("解释器 : %s" % sys.executable)
        print("工作目录: %s" % HERE)
        print("配置   : %s" % cfg_path)

    try:
        cfg = load_config(cfg_path)
        if debug:
            print("素材目录: %s" % os.path.join(HERE, cfg["sprite_dir"]))
        pet = Pet(cfg)
    except BaseException as exc:  # noqa: BLE001 - 需要给出人话提示
        report_fatal(exc, debug)
        raise SystemExit(1)

    if "--selftest" in sys.argv:
        pet.selftest(out=os.path.join(HERE, "selftest_frame.png"))
        pet.root.destroy()
        return

    if debug:
        print("窗口位置: (%d, %d)  尺寸: %dx%d" % (
            pet.pos_x, pet.pos_y, pet.win_w, pet.win_h))
        print("已启动，右键桌宠可以退出。")
    try:
        pet.run()
    except KeyboardInterrupt:
        pass
    except BaseException as exc:  # noqa: BLE001
        report_fatal(exc, debug)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
