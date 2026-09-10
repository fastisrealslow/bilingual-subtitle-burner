"""Suppress detector jitter without inventing source camera movement."""
import math

VERSION = 2026091001


class StableFraming:
    """Lock shot scale, hold small motion, rate-limit only necessary pans.

    Camera cuts come from source pixels, never a fluctuating face box. Floating
    point pan positions avoid the old two-pixel rounding feedback loop.
    """
    def __init__(self, fps):
        self.fps = fps
        self.previous_image = None
        self.box = None
        self.pending_cut = False
        self.cut_frames = []
        self.pan_frames = 0
        self.held_frames = 0
        self.max_pan_output_px = 0.
        self.samples = []

    def observe(self, frame, n):
        import cv2
        import numpy as np
        small = cv2.resize(frame, (64, 36)).astype(np.float32)
        if self.previous_image is not None:
            difference = np.abs(small - self.previous_image).mean(axis=2)
            # Broad image changes distinguish a cut from a mouth or head moving.
            cut = float(difference.mean()) > 24 and float((difference > 22).mean()) > .55
            if cut:
                self.pending_cut = True
                self.cut_frames.append(n)
        self.previous_image = small

    def update(self, proposed, face, width, height, n):
        reset = self.box is None or self.pending_cut
        if reset:
            self.box = tuple(map(float, proposed))
            self.pending_cut = False
        else:
            x, y, w, h = self.box
            fx, fy, fw, fh = map(float, face[:4])
            # Only follow a face approaching the reserved head/side margins.
            # Do not continuously chase its centre or change the zoom factor.
            dx = min(0., fx - (x + .12*w)) + max(0., fx+fw - (x + .88*w))
            dy = min(0., fy-.18*fh - (y + .04*h)) + max(0., fy+fh - (y + .91*h))
            limit = w * .12 / self.fps
            dx = max(-limit, min(limit, dx))
            dy = max(-limit, min(limit, dy))
            nx, ny = max(0., min(width-w, x+dx)), max(0., min(height-h, y+dy))
            movement = math.hypot((nx-x)*632/w, (ny-y)*470/h)
            self.max_pan_output_px = max(self.max_pan_output_px, movement)
            self.pan_frames += int(movement > .001)
            self.held_frames += int(movement <= .001)
            self.box = (nx, ny, w, h)
        box = tuple(round(v) for v in self.box)
        if reset or n % max(1, round(self.fps)) == 0:
            self.samples.append(dict(frame=n, crop=list(box), reset=reset))
        return box

    def proof(self):
        return dict(version=VERSION, policy='shot_scale_lock_deadzone_pan',
                    cut_frames=self.cut_frames, pan_frames=self.pan_frames,
                    held_frames=self.held_frames, max_pan_output_px=self.max_pan_output_px,
                    crop_samples=self.samples)
