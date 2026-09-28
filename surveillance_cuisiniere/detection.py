"""Détection des pastilles sur les boutons de la cuisinière (sans dépendance réseau).

Toutes les coordonnées de calibration sont normalisées (0..1) par rapport à l'image,
ce qui permet de changer de flux (HD / SD) sans refaire les zones.
"""
import math
import cv2
import numpy as np

WORK_W = 1280  # largeur de travail (l'image est redimensionnée à cette largeur)


def to_work(frame):
    h, w = frame.shape[:2]
    if w != WORK_W:
        frame = cv2.resize(frame, (WORK_W, int(round(h * WORK_W / w))), interpolation=cv2.INTER_AREA)
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return frame, gray


def rect_poly(r, W, H):
    """Rectangle normalisé [x, y, w, h] -> 4 coins en pixels."""
    x, y, w, h = r
    return np.array([[x * W, y * H], [(x + w) * W, y * H], [(x + w) * W, (y + h) * H], [x * W, (y + h) * H]],
                    dtype=np.float32)


def find_bright(gray, mask, thr_abs, contrast, min_area, max_area, prefer=None):
    """Cherche la tache lumineuse (pastille) dans le masque.

    Retourne (cx, cy, aire) ou None. Une tache trop grande (main éclairée en IR…) est ignorée.
    """
    vals = gray[mask > 0]
    if vals.size == 0:
        return None
    med = float(np.median(vals))
    # Seuil relatif au point le plus lumineux : la pastille (rétroréfléchissante) est
    # toujours ce qu'il y a de plus clair ; ça évite de coller le plan de travail clair
    # (très lumineux en infrarouge) à la pastille.
    peak = float(np.percentile(vals, 99.8))
    t = max(thr_abs, med + contrast, 0.85 * peak)
    bw = ((gray >= t) & (mask > 0)).astype(np.uint8)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, _, stats, cents = cv2.connectedComponentsWithStats(bw, 8)
    best, best_score = None, None
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if a < min_area or a > max_area:
            continue
        cx, cy = cents[i]
        score = -a if prefer is None else math.hypot(cx - prefer[0], cy - prefer[1]) - 0.01 * a
        if best_score is None or score < best_score:
            best, best_score = (float(cx), float(cy), int(a)), score
    return best


def circle_mask(shape, c, r):
    m = np.zeros(shape, np.uint8)
    cv2.circle(m, (int(c[0]), int(c[1])), int(r), 255, -1)
    return m


def poly_mask(shape, poly):
    m = np.zeros(shape, np.uint8)
    cv2.fillPoly(m, [poly.astype(np.int32)], 255)
    return m


class Detector:
    """Transforme une image en états bruts : 'eteint', 'allume', 'cache' ; ou None si hors position."""

    def __init__(self, calib, opts):
        self.c = calib
        self.thr = opts.get("seuil_luminosite", 150)
        self.contrast = opts.get("contraste_min", 60)
        self.search = opts.get("rayon_recherche_repere", 0.06)

    # --- repères -------------------------------------------------------------
    def locate_refs(self, gray):
        H, W = gray.shape
        found = []
        for ref in self.c["refs"]:
            p = (ref["x"] * W, ref["y"] * H)
            a = ref["aire"] * W * H
            m = circle_mask(gray.shape, p, self.search * W)
            b = find_bright(gray, m, self.thr, self.contrast, max(3, a * 0.2), a * 6, prefer=p)
            if b is None:
                return None, None
            found.append(b[:2])
        p1 = np.array([self.c["refs"][0]["x"] * W, self.c["refs"][0]["y"] * H])
        p2 = np.array([self.c["refs"][1]["x"] * W, self.c["refs"][1]["y"] * H])
        q1, q2 = np.array(found[0]), np.array(found[1])
        dp, dq = p2 - p1, q2 - q1
        s = np.linalg.norm(dq) / max(1e-6, np.linalg.norm(dp))
        ang = math.atan2(dq[1], dq[0]) - math.atan2(dp[1], dp[0])
        ang = (ang + math.pi) % (2 * math.pi) - math.pi
        # Les deux repères doivent garder la même distance et le même angle,
        # sinon ce sont d'autres taches lumineuses (caméra tournée vers la pièce).
        if not (0.88 < s < 1.12) or abs(math.degrees(ang)) > 7:
            return None, found
        R = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]]) * s

        def T(pts):
            pts = np.asarray(pts, dtype=np.float32).reshape(-1, 2)
            return ((pts - p1) @ R.T + q1).astype(np.float32)

        return T, found

    # --- boutons -------------------------------------------------------------
    def analyse(self, gray):
        H, W = gray.shape
        T, refs = self.locate_refs(gray)
        if T is None:
            return None, {"refs": refs}
        out, geo = [], []
        for k in self.c["boutons"]:
            zone = T(rect_poly(k["zone"], W, H))
            off = T(rect_poly(k["off"], W, H))
            a = k["aire"] * W * H
            b = find_bright(gray, poly_mask(gray.shape, zone), self.thr, self.contrast, max(3, a * 0.2), a * 6)
            if b is None:
                st = "cache"
            elif cv2.pointPolygonTest(off.reshape(-1, 1, 2), (b[0], b[1]), False) >= 0:
                st = "eteint"
            else:
                st = "allume"
            out.append(st)
            geo.append({"zone": zone, "off": off, "blob": b})
        return out, {"refs": refs, "boutons": geo}

    # --- calibration ---------------------------------------------------------
    @staticmethod
    def build_calibration(gray, refs_clicks, boutons, opts):
        """Construit la calibration à partir des clics de l'utilisateur.

        Les boutons doivent être sur OFF : on mesure la taille de chaque pastille.
        Retourne (calib, erreurs).
        """
        H, W = gray.shape
        thr, contrast = opts.get("seuil_luminosite", 150), opts.get("contraste_min", 60)
        errs, refs, ks = [], [], []
        for i, (x, y) in enumerate(refs_clicks):
            p = (x * W, y * H)
            b = find_bright(gray, circle_mask(gray.shape, p, 0.03 * W), thr, contrast, 3, 0.01 * W * H, prefer=p)
            if b is None:
                errs.append(f"Repère {'gauche' if i == 0 else 'droit'} : aucune pastille lumineuse trouvée près du clic.")
                continue
            refs.append({"x": b[0] / W, "y": b[1] / H, "aire": b[2] / (W * H)})
        for i, k in enumerate(boutons):
            off = rect_poly(k["off"], W, H)
            m = poly_mask(gray.shape, rect_poly(k["zone"], W, H))
            b = find_bright(gray, m, thr, contrast, 3, 0.02 * W * H)
            if b is not None and cv2.pointPolygonTest(off.reshape(-1, 1, 2), (b[0], b[1]), False) < 0:
                b = None
            if b is None:
                errs.append(f"Bouton {i + 1} : pastille introuvable dans la zone OFF (le bouton est-il sur OFF ?).")
                continue
            ks.append({"zone": k["zone"], "off": k["off"], "aire": b[2] / (W * H)})
        if errs:
            return None, errs
        return {"refs": refs, "boutons": ks}, []


def annotate(frame, states, geo, validated=None, labels=None):
    img = frame.copy() if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    col = {"eteint": (80, 200, 80), "allume": (40, 40, 255), "cache": (0, 200, 255)}
    for r in (geo or {}).get("refs") or []:
        cv2.circle(img, (int(r[0]), int(r[1])), 14, (255, 200, 0), 2)
    for i, g in enumerate((geo or {}).get("boutons") or []):
        st = states[i] if states else "cache"
        cv2.polylines(img, [g["zone"].astype(np.int32)], True, (180, 180, 180), 1)
        cv2.polylines(img, [g["off"].astype(np.int32)], True, (80, 200, 80), 2)
        if g["blob"]:
            cv2.circle(img, (int(g["blob"][0]), int(g["blob"][1])), 6, col[st], -1)
        x, y = g["zone"][0]
        txt = f"{i + 1}: " + (labels or {}).get(st, st)
        if validated and validated[i] is not None and validated[i] != st:
            txt += f" ({(labels or {}).get(validated[i], validated[i])})"
        cv2.putText(img, txt, (int(x), int(y) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4)
        cv2.putText(img, txt, (int(x), int(y) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.55, col[st], 1)
    return img


class MotionDetector:
    """Détection de mouvement simple (différence entre images successives).

    - Une image où presque tout change (caméra qui pivote, bascule jour/nuit) est ignorée,
      et il faut ensuite quelques images stables avant de juger à nouveau.
    - Il faut 2 images consécutives « avec mouvement » pour conclure (anti-bruit).
    """

    def __init__(self, seuil_pct=0.3, stable_min=3):
        self.seuil = seuil_pct / 100.0
        self.stable_min = stable_min
        self.prev, self.stable, self.hits = None, 0, 0
        self.last_frac = 0.0

    def reset(self):
        self.prev, self.stable, self.hits = None, 0, 0

    def update(self, gray):
        """Retourne True si un mouvement est détecté sur cette image."""
        h, w = gray.shape
        small = cv2.GaussianBlur(cv2.resize(gray, (320, max(1, int(320 * h / w)))), (7, 7), 0)
        prev, self.prev = self.prev, small
        if prev is None or prev.shape != small.shape:
            self.stable, self.hits = 0, 0
            return False
        frac = float((cv2.absdiff(small, prev) > 25).mean())
        self.last_frac = frac
        if frac > 0.25:  # caméra en mouvement ou changement d'éclairage global
            self.stable, self.hits = 0, 0
            return False
        self.stable += 1
        if self.stable < self.stable_min:
            return False
        self.hits = self.hits + 1 if frac >= self.seuil else 0
        return self.hits >= 2
