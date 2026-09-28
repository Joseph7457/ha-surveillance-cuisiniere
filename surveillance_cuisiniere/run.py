#!/usr/bin/env python3
"""Add-on Home Assistant : surveillance des boutons de la cuisinière.

- lit le flux RTSP de la caméra Tapo en continu ;
- quand les deux pastilles de repère sont visibles (caméra sur la position « boutons »),
  recale les zones et détermine l'état de chaque bouton ;
- anti-rebond, puis publication dans Home Assistant via MQTT (découverte automatique) ;
- page web (ingress) pour dessiner les zones et voir la détection en direct.
"""
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
import cv2  # noqa: E402
import paho.mqtt.client as mqtt  # noqa: E402

from detection import Detector, MotionDetector, annotate, to_work  # noqa: E402

DATA = os.environ.get("DATA_DIR", "/data")
HERE = os.path.dirname(os.path.abspath(__file__))
CALIB_FILE = os.path.join(DATA, "calibration.json")
STATE_FILE = os.path.join(DATA, "etats.json")
LABELS = {"eteint": "éteint", "allume": "allumé", "cache": "caché"}
IMG_LABELS = {"eteint": "OFF", "allume": "ON", "cache": "cache"}  # OpenCV ne sait pas écrire les accents

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cuisiniere")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


OPTS = {
    "camera_ip": "",
    "camera_utilisateur": "",
    "camera_mot_de_passe": "",
    "flux": "stream1",
    "images_par_seconde": 3,
    "anti_rebond_images": 5,
    "seuil_luminosite": 150,
    "contraste_min": 60,
    "rayon_recherche_repere": 0.06,
    "mouvement_seuil_pct": 0.3,
    "presence_maintien_secondes": 90,
}
OPTS.update(load_json(os.path.join(DATA, "options.json"), {}))


def rtsp_url():
    u = urllib.parse.quote(OPTS["camera_utilisateur"], safe="")
    p = urllib.parse.quote(OPTS["camera_mot_de_passe"], safe="")
    return f"rtsp://{u}:{p}@{OPTS['camera_ip']}:554/{OPTS['flux']}"


# --------------------------------------------------------------------------- caméra
class Grabber(threading.Thread):
    """Lit le flux en continu et garde seulement la dernière image."""

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.frame, self.stamp, self.connected = None, 0.0, False

    def run(self):
        wait = 15
        while True:
            cap = cv2.VideoCapture(rtsp_url(), cv2.CAP_FFMPEG)
            if not cap.isOpened():
                # Attente de plus en plus longue : trop d'échecs d'identification
                # font bloquer temporairement la caméra Tapo.
                log.warning("Impossible d'ouvrir le flux %s (nouvel essai dans %d s)", OPTS["flux"], wait)
                self.connected = False
                time.sleep(wait)
                wait = min(wait * 2, 600)
                continue
            wait = 15
            log.info("Flux vidéo connecté (%s)", OPTS["flux"])
            self.connected = True
            fails = 0
            while fails < 50:
                ok, fr = cap.read()
                if not ok:
                    fails += 1
                    time.sleep(0.1)
                    continue
                fails = 0
                with self.lock:
                    self.frame, self.stamp = fr, time.time()
            log.warning("Flux vidéo perdu, reconnexion…")
            self.connected = False
            cap.release()
            time.sleep(3)

    def latest(self):
        with self.lock:
            return self.frame, self.stamp


# --------------------------------------------------------------------------- MQTT
def mqtt_settings():
    tok = os.environ.get("SUPERVISOR_TOKEN")
    if tok:
        try:
            req = urllib.request.Request("http://supervisor/services/mqtt",
                                         headers={"Authorization": f"Bearer {tok}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.load(r)["data"]
            return d["host"], int(d["port"]), d.get("username"), d.get("password")
        except Exception as e:  # noqa: BLE001
            log.error("Service MQTT introuvable (Mosquitto est-il installé et démarré ?) : %s", e)
    return os.environ.get("MQTT_HOST", "core-mosquitto"), 1883, os.environ.get("MQTT_USER"), os.environ.get("MQTT_PASS")


class Publisher:
    BASE = "cuisiniere"

    def __init__(self, n):
        self.n = n
        host, port, user, pwd = mqtt_settings()
        try:
            self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="cuisiniere-addon")
        except AttributeError:
            self.c = mqtt.Client(client_id="cuisiniere-addon")
        if user:
            self.c.username_pw_set(user, pwd)
        self.c.will_set(f"{self.BASE}/disponible", "offline", retain=True)
        self.c.on_connect = self._on_connect
        self.c.connect_async(host, port, 30)
        self.c.loop_start()
        self.last = {}

    def _on_connect(self, *_):
        log.info("Connecté à MQTT")
        self.discovery()
        self.c.publish(f"{self.BASE}/disponible", "online", retain=True)
        for t, v in list(self.last.items()):
            self.c.publish(t, v, retain=True)

    def pub(self, topic, value, force=False):
        t = f"{self.BASE}/{topic}"
        if force or self.last.get(t) != value:
            self.last[t] = value
            self.c.publish(t, value, retain=True)

    def discovery(self):
        dev = {"identifiers": ["cuisiniere_addon"], "name": "Surveillance cuisinière",
               "manufacturer": "Add-on maison", "model": "Pastilles + OpenCV"}
        avail = {"availability_topic": f"{self.BASE}/disponible", "device": dev}

        def cfg(comp, oid, d):
            d.update(avail)
            d["unique_id"] = f"cuisiniere_{oid}"
            d["object_id"] = f"cuisiniere_{oid}"
            self.c.publish(f"homeassistant/{comp}/cuisiniere/{oid}/config", json.dumps(d), retain=True)

        for i in range(1, self.n + 1):
            cfg("binary_sensor", f"feu_{i}", {
                "name": f"Feu {i}", "state_topic": f"{self.BASE}/feu_{i}/etat", "device_class": "heat",
                "payload_on": "ON", "payload_off": "OFF", "icon": "mdi:stove"})
            cfg("sensor", f"feu_{i}_vision", {
                "name": f"Feu {i} vision", "state_topic": f"{self.BASE}/feu_{i}/vision",
                "entity_category": "diagnostic", "icon": "mdi:eye"})
        cfg("binary_sensor", "un_feu_allume", {
            "name": "Un feu allumé", "state_topic": f"{self.BASE}/un_feu/etat", "device_class": "heat",
            "payload_on": "ON", "payload_off": "OFF", "icon": "mdi:fire"})
        cfg("sensor", "position", {
            "name": "Vue caméra", "state_topic": f"{self.BASE}/position", "icon": "mdi:cctv"})
        cfg("sensor", "derniere_vue_boutons", {
            "name": "Dernière vue des boutons", "state_topic": f"{self.BASE}/derniere_vue",
            "device_class": "timestamp", "icon": "mdi:clock-check"})
        cfg("camera", "image", {"name": "Image des boutons", "topic": f"{self.BASE}/image"})
        cfg("binary_sensor", "presence", {
            "name": "Présence cuisine", "state_topic": f"{self.BASE}/presence", "device_class": "occupancy",
            "payload_on": "ON", "payload_off": "OFF"})
        cfg("sensor", "dernier_mouvement", {
            "name": "Dernier mouvement", "state_topic": f"{self.BASE}/dernier_mouvement",
            "device_class": "timestamp", "icon": "mdi:motion-sensor"})


# --------------------------------------------------------------------------- logique
class Engine:
    def __init__(self):
        self.grab = Grabber()
        self.calib = load_json(CALIB_FILE, None)
        n = len(self.calib["boutons"]) if self.calib else 4
        saved = load_json(STATE_FILE, {})
        self.validated = saved.get("etats", [None] * n)
        self.validated = (self.validated + [None] * n)[:n]
        self.cand, self.count = [None] * n, [0] * n
        self.raw, self.position, self.last_seen = [None] * n, "inconnue", saved.get("derniere_vue")
        self.annotated, self.last_img_pub, self.last_save, self.last_seen_pub = None, 0.0, 0.0, 0.0
        self.lock = threading.Lock()
        self.pub = Publisher(n)
        self.det = Detector(self.calib, OPTS) if self.calib else None
        self.motion = MotionDetector(float(OPTS["mouvement_seuil_pct"]))
        self.last_motion, self.presence, self.last_stamp = 0.0, None, None
        for i, v in enumerate(self.validated):
            if v is not None:
                self.pub.pub(f"feu_{i + 1}/etat", "ON" if v == "allume" else "OFF")
        self._pub_any()

    def set_calibration(self, calib):
        with self.lock:
            n = len(calib["boutons"])
            self.calib, self.det = calib, Detector(calib, OPTS)
            if len(self.validated) != n:
                self.validated = [None] * n
            self.cand, self.count, self.raw = [None] * n, [0] * n, [None] * n
        save_json(CALIB_FILE, calib)
        self.pub.n = n
        self.pub.discovery()

    def _pub_any(self):
        if all(v is None for v in self.validated):
            return
        self.pub.pub("un_feu/etat", "ON" if any(v == "allume" for v in self.validated) else "OFF")

    def step(self):
        frame, stamp = self.grab.latest()
        if frame is None or time.time() - stamp > 10:
            self.pub.pub("position", "caméra injoignable")
            return
        if stamp == self.last_stamp:  # pas de nouvelle image depuis la dernière analyse
            return
        self.last_stamp = stamp
        work, gray = to_work(frame)
        moved = self.motion.update(gray)
        if not self.det:
            self.pub.pub("position", "non calibré")
            self.annotated = work
            return
        with self.lock:
            states, geo = self.det.analyse(gray)
            if states is None:
                # Caméra tournée vers la pièce (ou repères masqués) : on garde les derniers états
                # et on cherche du mouvement (= quelqu'un dans la cuisine).
                self.position = "ailleurs"
                self.pub.pub("position", "ailleurs")
                self.annotated = annotate(work, None, geo)
                if moved:
                    self._motion_seen()
                self._pub_presence()
                return
            # Vue des boutons : on n'utilise PAS le mouvement ici (une casserole qui bout ou
            # de la vapeur ne doivent pas faire croire que quelqu'un est présent).
            self.position = "boutons"
            self.raw = states
            self.last_seen = datetime.now(timezone.utc).isoformat(timespec="seconds")
            changed = False
            for i, st in enumerate(states):
                self.pub.pub(f"feu_{i + 1}/vision", LABELS[st])
                if st == "cache":  # main, casserole… : on ne conclut rien
                    self.cand[i], self.count[i] = None, 0
                    continue
                if st == self.cand[i]:
                    self.count[i] += 1
                else:
                    self.cand[i], self.count[i] = st, 1
                if self.count[i] >= OPTS["anti_rebond_images"] and self.validated[i] != st:
                    self.validated[i] = st
                    changed = True
                    log.info("Feu %d : %s", i + 1, LABELS[st])
                    self.pub.pub(f"feu_{i + 1}/etat", "ON" if st == "allume" else "OFF")
            self.annotated = annotate(work, states, geo, self.validated, IMG_LABELS)
        self.pub.pub("position", "boutons")
        self._pub_presence()
        if changed or time.time() - self.last_seen_pub > 30:
            self.pub.pub("derniere_vue", self.last_seen)
            self.last_seen_pub = time.time()
        if changed:
            self._pub_any()
            save_json(STATE_FILE, {"etats": self.validated, "derniere_vue": self.last_seen})
        if changed or time.time() - self.last_img_pub > 15:
            ok, jpg = cv2.imencode(".jpg", self.annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                self.pub.c.publish(f"{Publisher.BASE}/image", jpg.tobytes(), retain=True)
                self.last_img_pub = time.time()
        if time.time() - self.last_save > 60:
            save_json(STATE_FILE, {"etats": self.validated, "derniere_vue": self.last_seen})
            self.last_save = time.time()

    def _motion_seen(self):
        now = time.time()
        if now - self.last_motion > 5:
            self.pub.pub("dernier_mouvement", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.last_motion = now

    def _pub_presence(self):
        p = time.time() - self.last_motion < float(OPTS["presence_maintien_secondes"])
        if p != self.presence:
            self.presence = p
            log.info("Présence cuisine : %s", "oui" if p else "non")
        self.pub.pub("presence", "ON" if p else "OFF")

    def loop(self):
        self.grab.start()
        period = 1.0 / max(0.5, float(OPTS["images_par_seconde"]))
        while True:
            t0 = time.time()
            try:
                self.step()
            except Exception:  # noqa: BLE001
                log.exception("Erreur pendant l'analyse")
            time.sleep(max(0.0, period - (time.time() - t0)))

    def status(self):
        return {"position": self.position, "flux_connecte": self.grab.connected, "presence": bool(self.presence),
                "dernier_mouvement": int(time.time() - self.last_motion) if self.last_motion else None,
                "calibre": self.calib is not None, "derniere_vue": self.last_seen,
                "boutons": [{"vision": LABELS.get(r, "—") if r else "—",
                             "etat": LABELS.get(v, "inconnu") if v else "inconnu"}
                            for r, v in zip(self.raw, self.validated)]}


ENGINE = None


# --------------------------------------------------------------------------- web
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _jpg(self, img):
        if img is None:
            return self._send(503, b"pas d'image", "text/plain")
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        self._send(200, jpg.tobytes(), "image/jpeg")

    def do_GET(self):
        path = self.path.split("?")[0]
        p = path.rsplit("/", 1)[-1]
        if p in ("", "index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if p == "snapshot.jpg":
            fr, _ = ENGINE.grab.latest()
            return self._jpg(to_work(fr)[0] if fr is not None else None)
        if p == "annotated.jpg":
            return self._jpg(ENGINE.annotated)
        if p == "calibration":
            return self._json(ENGINE.calib or {})
        if p == "status":
            return self._json(ENGINE.status())
        self._send(404, b"introuvable", "text/plain")

    def do_POST(self):
        p = self.path.split("?")[0].rsplit("/", 1)[-1]
        if p != "calibration":
            return self._send(404, b"introuvable", "text/plain")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            fr, _ = ENGINE.grab.latest()
            if fr is None:
                return self._json({"ok": False, "erreurs": ["Pas d'image de la caméra."]})
            _, gray = to_work(fr)
            calib, errs = Detector.build_calibration(gray, body["refs"], body["boutons"], OPTS)
            if errs:
                return self._json({"ok": False, "erreurs": errs})
            ENGINE.set_calibration(calib)
            log.info("Nouvelle calibration enregistrée (%d boutons)", len(calib["boutons"]))
            self._json({"ok": True})
        except Exception as e:  # noqa: BLE001
            log.exception("Calibration")
            self._json({"ok": False, "erreurs": [str(e)]}, 400)


def main():
    global ENGINE
    if not OPTS["camera_ip"]:
        log.error("Renseignez l'adresse IP de la caméra dans l'onglet Configuration.")
    if not OPTS["camera_utilisateur"]:
        log.error("Renseignez l'utilisateur et le mot de passe du compte caméra dans l'onglet Configuration.")
    ENGINE = Engine()
    srv = ThreadingHTTPServer(("0.0.0.0", 8099), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("Interface disponible dans la barre latérale (Cuisinière)")
    ENGINE.loop()


if __name__ == "__main__":
    main()
