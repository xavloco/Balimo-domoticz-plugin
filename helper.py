#!/usr/bin/env python3
"""Processus enfant du plugin Balimo.

Il parle à tinytuya dans un processus Python propre et dialogue avec le plugin
en JSON, une ligne par message. Quand le plugin disparaît, il s'arrête aussitôt
et libère la connexion au distributeur.
"""
import base64
import json
import os
import queue
import subprocess
import sys
import threading
import time

cfg = json.loads(sys.stdin.readline())
LIB = cfg["lib"]
FICHIER_CLE = cfg["fichier_cle"]
if LIB not in sys.path:
    sys.path.insert(0, LIB)

DP_MANUEL = 3
verrou = threading.Lock()
commandes = queue.Queue()
version_ok = None


def envoyer(genre, **champs):
    champs["t"] = genre
    with verrou:
        sys.stdout.write(json.dumps(champs) + "\n")
        sys.stdout.flush()


def ecouter_plugin():
    for ligne in sys.stdin:
        try:
            commandes.put(json.loads(ligne))
        except ValueError:
            pass
    os._exit(0)  # le plugin est parti : on libère le distributeur


def charger_tinytuya():
    try:
        import tinytuya
        return tinytuya
    except ImportError:
        pass
    envoyer("log", m="Installation de tinytuya (première utilisation)...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "--target", LIB, "tinytuya"],
            timeout=300, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        import importlib
        importlib.invalidate_caches()
        import tinytuya
        return tinytuya
    except Exception as e:
        envoyer("err", m="Installation de tinytuya impossible (%s). Lance : sudo apt install python3-pip" % e)
        return None


def nouveau_cloud(tt):
    return tt.Cloud(
        apiRegion=cfg["region"],
        apiKey=cfg["access_id"],
        apiSecret=cfg["access_secret"],
        apiDeviceID=cfg["device_id"],
    )


def cle_locale(tt, forcer=False):
    if not forcer and os.path.exists(FICHIER_CLE):
        try:
            with open(FICHIER_CLE) as f:
                d = json.load(f)
            if d.get("id") == cfg["device_id"] and d.get("key"):
                return d["key"]
        except Exception:
            pass
    liste = nouveau_cloud(tt).getdevices()
    if not isinstance(liste, list):
        raise RuntimeError("cloud Tuya : %s" % liste)
    for appareil in liste:
        if appareil.get("id") == cfg["device_id"] and appareil.get("key"):
            fd = os.open(FICHIER_CLE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"id": appareil["id"], "key": appareil["key"]}, f)
            envoyer("log", m="Clé locale récupérée depuis le cloud Tuya")
            return appareil["key"]
    raise RuntimeError("Device ID introuvable dans ton compte Tuya")


def session(tt):
    global version_ok
    cle = cle_locale(tt)
    if cfg["version"] == "auto":
        versions = ["3.4", "3.3", "3.5"]
        if version_ok in versions:
            versions.remove(version_ok)
            versions.insert(0, version_ok)
    else:
        versions = [cfg["version"]]
    dev, etat, erreurs = None, None, []
    for v in versions:
        essai = tt.Device(cfg["device_id"], cfg["ip"], cle, version=float(v))
        essai.set_socketPersistent(True)
        essai.set_socketTimeout(5)
        reponse = essai.status()
        if isinstance(reponse, dict) and "dps" in reponse:
            dev, etat = essai, reponse
            if v != version_ok:
                envoyer("log", m="Protocole détecté : %s" % v)
            version_ok = v
            break
        erreurs.append("%s : %s" % (v, reponse))
        try:
            essai.close()
        except Exception:
            pass
    if dev is None:
        texte = " / ".join(erreurs)
        if "914" in texte or "key" in texte.lower():
            cle_locale(tt, forcer=True)
        raise RuntimeError(
            "appareil injoignable : vérifie l'adresse IP, la version du protocole "
            "et qu'aucun autre programme n'est connecté au distributeur (%s)" % texte)
    try:
        # Jamais de commande gardée en attente pendant une coupure
        while not commandes.empty():
            commandes.get_nowait()
        envoyer("up")
        envoyer("dps", d=etat["dps"], init=True)
        dernier_coeur = time.time()
        while True:
            try:
                while True:
                    c = commandes.get_nowait()
                    if c.get("c") == "manuel":
                        reponse = dev.set_value(DP_MANUEL, int(c["n"]))
                        if isinstance(reponse, dict) and "Error" in reponse:
                            envoyer("err", m="Distribution refusée : %s" % reponse["Error"])
            except queue.Empty:
                pass
            if time.time() - dernier_coeur > 10:
                dev.heartbeat(nowait=True)
                dernier_coeur = time.time()
            data = dev.receive()
            if isinstance(data, dict):
                if "dps" in data:
                    envoyer("dps", d=data["dps"])
                elif "Error" in data:
                    texte = str(data["Error"])
                    if "timeout" not in texte.lower() and "timed out" not in texte.lower():
                        raise RuntimeError(texte)
    finally:
        try:
            dev.close()
        except Exception:
            pass


def decrire_programme(valeur):
    """Renvoie le texte du programme et la liste [jours, heure, minute, portions, actif]."""
    octets = base64.b64decode(valeur)
    repas = [list(octets[i:i + 5]) for i in range(0, len(octets) - 4, 5)]
    if not repas:
        return "Aucun repas programmé", []
    tous_les_jours = all(r[0] == 127 for r in repas)
    morceaux = []
    for jours, heure, minute, portions, actif in repas:
        texte = "%02d:%02d (%d)" % (heure, minute, portions)
        if not tous_les_jours:
            texte += " [jours %d]" % jours
        if not actif:
            texte += " désactivé"
        morceaux.append(texte)
    return " · ".join(morceaux), repas


def lire_programme(tt):
    reponse = nouveau_cloud(tt).getstatus(cfg["device_id"])
    if not isinstance(reponse, dict):
        raise RuntimeError(str(reponse))
    if "Error" in reponse:
        raise RuntimeError("cloud Tuya : %s %s" % (reponse.get("Error"), reponse.get("Payload") or ""))
    for element in reponse.get("result") or []:
        if element.get("code") == "meal_plan":
            return decrire_programme(element["value"])
    raise RuntimeError("meal_plan absent de la réponse du cloud")


def boucle_programme(tt):
    while True:
        try:
            texte, repas = lire_programme(tt)
            envoyer("plan", m=texte, r=repas)
        except Exception as e:
            envoyer("log", m="Programme indisponible : %s" % e)
        time.sleep(3600)


def main():
    threading.Thread(target=ecouter_plugin, daemon=True).start()
    tt = charger_tinytuya()
    if tt is None:
        return 1
    if cfg.get("debug"):
        import logging
        gestionnaire = logging.FileHandler(os.path.join(os.path.dirname(FICHIER_CLE), "debug.log"))
        gestionnaire.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        journal = logging.getLogger("tinytuya")
        journal.addHandler(gestionnaire)
        journal.setLevel(logging.DEBUG)
    threading.Thread(target=boucle_programme, args=(tt,), daemon=True).start()
    while True:
        try:
            session(tt)
        except Exception as e:
            envoyer("down")
            envoyer("log", m="Connexion perdue, nouvelle tentative dans 30 s : %s" % e)
        time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())
