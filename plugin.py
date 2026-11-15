"""
<plugin key="BalimoTuya" name="Balimo - distributeur de croquettes (Tuya)" author="Xavier" version="1.0.0">
    <description>
        <h2>Balimo (Tuya)</h2>
        Contrôle local du distributeur de croquettes.<br/>
        Renseigne l'adresse IP du distributeur, les identifiants de ton projet Tuya (Access ID et Access Secret)
        et le Device ID. La clé locale est récupérée automatiquement.<br/>
        Le nombre de portions se choisit avec l'appareil "Portions" dans Domoticz.<br/>
        Choix "Personnalisé" : écris les numéros des appareils voulus, séparés par des virgules (ex : 1,2,3,13).<br/>
        <b>1</b> Distribuer · <b>2</b> État · <b>3</b> Batterie · <b>4</b> Portions distribuées · <b>5</b> En charge ·
        <b>6</b> Dernière distribution · <b>7</b> Programme · <b>8</b> Portions (sélecteur) ·
        <b>9</b> Portions aujourd'hui · <b>10</b> Alerte distribution · <b>11</b> Connecté ·
        <b>12</b> Point 13 (inconnu) · <b>13</b> Prochain repas
    </description>
    <params>
        <param field="Address" label="Adresse IP du distributeur" width="200px" required="true"/>
        <param field="Username" label="Access ID (Tuya)" width="300px" required="true"/>
        <param field="Password" label="Access Secret (Tuya)" width="300px" required="true" password="true"/>
        <param field="Mode1" label="Device ID" width="300px" required="true"/>
        <param field="Mode2" label="Région Tuya" width="200px">
            <options>
                <option label="Europe centrale (eu)" value="eu" default="true"/>
                <option label="Europe ouest (eu-w)" value="eu-w"/>
                <option label="Etats-Unis ouest (us)" value="us"/>
                <option label="Etats-Unis est (us-e)" value="us-e"/>
                <option label="Inde (in)" value="in"/>
                <option label="Chine (cn)" value="cn"/>
            </options>
        </param>
        <param field="Mode3" label="Version du protocole" width="100px">
            <options>
                <option label="Automatique (recommandé)" value="auto" default="true"/>
                <option label="3.3" value="3.3"/>
                <option label="3.4" value="3.4"/>
                <option label="3.5" value="3.5"/>
            </options>
        </param>
        <param field="Mode4" label="Appareils à afficher" width="200px">
            <options>
                <option label="Tous" value="tous" default="true"/>
                <option label="Essentiel (1, 2, 3, 8, 10, 11, 13)" value="essentiel"/>
                <option label="Personnalisé (liste ci-dessous)" value="perso"/>
            </options>
        </param>
        <param field="Mode5" label="Liste personnalisée (numéros, ex : 1,2,3,13)" width="300px"/>
        <param field="Mode6" label="Journal détaillé" width="100px">
            <options>
                <option label="Non" value="0" default="true"/>
                <option label="Oui" value="1"/>
            </options>
        </param>
    </params>
</plugin>
"""
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from datetime import time as heure_du_jour

import Domoticz  # fourni par Domoticz

DOSSIER = os.path.dirname(os.path.realpath(__file__))
LIB = os.path.join(DOSSIER, "lib")
FICHIER_CLE = os.path.join(DOSSIER, "cle_locale.json")
HELPER = os.path.join(DOSSIER, "helper.py")

# Appareils Domoticz créés par le plugin
(U_DISTRIBUER, U_ETAT, U_BATTERIE, U_PORTIONS, U_CHARGE, U_DERNIERE, U_PROGRAMME,
 U_CHOIX, U_JOUR, U_ALERTE, U_CONNEXION, U_INCONNU, U_PROCHAIN) = range(1, 14)

# Points de données du distributeur (Tuya)
DP_ETAT, DP_BATTERIE, DP_CHARGE, DP_RAPPORT, DP_INCONNU = "4", "10", "11", "14", "13"
DP_CONNUS = {"1", "2", "3", "4", "10", "11", "14", "24", "25"}
ETATS = {"standby": "Au repos", "feeding": "Distribution en cours", "done": "Terminé"}

# Réglages de la surveillance des repas
AVANCE_REPAS = 120      # secondes : une distribution peut démarrer un peu avant l'heure
RETARD_REPAS = 300      # secondes : au-delà, le repas est considéré comme manqué
DUREE_MAX_DISTRIBUTION = 300

lien = None
portions = 1
dernier_message = None
derniere_demande = 0

repas = []                  # programme : [jours, heure, minute, portions, actif]
repas_etat = {}             # (date, heure, minute) -> "ok", "manque" ou "inconnu"
demarrages = []             # heures de début des distributions détectées
evenement = None            # distribution en cours : {"debut", "portions"}
derniere_requete = None     # (heure, portions) de notre dernier appui sur Distribuer
debut_plugin = None
derniere_deconnexion = None
total_jour = 0
date_jour = None
connexion_affichee = None
inconnus = {}
affichage = {}              # derniers textes envoyés à Domoticz, pour éviter les mises à jour inutiles


class Lien(threading.Thread):
    """Lance helper.py (qui parle à tinytuya) et relaie ses messages vers Domoticz."""

    def __init__(self, reglages):
        super().__init__(daemon=True)
        self.r = reglages
        self.sortie = queue.Queue()   # du distributeur vers Domoticz
        self.arret = threading.Event()
        self.connecte = threading.Event()
        self.processus = None
        self.verrou = threading.Lock()

    def envoyer(self, commande):
        with self.verrou:
            p = self.processus
            if p is None or p.poll() is not None:
                return False
            try:
                p.stdin.write(json.dumps(commande) + "\n")
                p.stdin.flush()
                return True
            except (OSError, ValueError):
                return False

    def run(self):
        while not self.arret.is_set():
            try:
                self.lancer()
            except Exception as e:
                self.sortie.put(("log", "Processus tinytuya : %s" % e))
            self.connecte.clear()
            self.arret.wait(15)

    def lancer(self):
        python = shutil.which("python3") or "/usr/bin/python3"
        # Le processus enfant ne doit pas hériter de l'environnement Python de Domoticz
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        p = subprocess.Popen(
            [python, HELPER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, cwd=DOSSIER, env=env,
        )
        with self.verrou:
            self.processus = p
        config = dict(self.r, lib=LIB, fichier_cle=FICHIER_CLE)
        p.stdin.write(json.dumps(config) + "\n")
        p.stdin.flush()
        for ligne in p.stdout:
            self.traiter(ligne)
        p.wait()

    def traiter(self, ligne):
        try:
            m = json.loads(ligne)
        except ValueError:
            if ligne.strip():
                self.sortie.put(("log", "tinytuya : " + ligne.strip()[:300]))
            return
        genre = m.get("t")
        if genre == "up":
            self.connecte.set()
            self.sortie.put(("log", "Connecté au distributeur"))
        elif genre == "down":
            self.connecte.clear()
        elif genre == "dps":
            self.sortie.put(("dps", (m.get("d") or {}, bool(m.get("init")))))
        elif genre == "plan":
            self.sortie.put(("plan", (m.get("m", ""), m.get("r") or [])))
        elif genre == "log":
            self.sortie.put(("log", m.get("m", "")))
        elif genre == "err":
            self.sortie.put(("erreur", m.get("m", "")))

    def arreter(self):
        self.arret.set()
        self.connecte.clear()
        with self.verrou:
            p = self.processus
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()


# ---------------------------------------------------------------- appareils Domoticz

TOUS = set(range(1, 14))
ESSENTIEL = {U_DISTRIBUER, U_ETAT, U_BATTERIE, U_CHOIX, U_ALERTE, U_CONNEXION, U_PROCHAIN}


def appareils_voulus():
    """Ensemble des numéros d'appareils à créer, selon les réglages du matériel."""
    mode = Parameters.get("Mode4", "tous")
    if mode == "essentiel":
        return set(ESSENTIEL)
    if mode == "perso":
        texte = str(Parameters.get("Mode5", "")).replace(";", ",").replace(" ", ",")
        try:
            liste = {int(x) for x in texte.split(",") if x}
        except ValueError:
            liste = set()
        liste &= TOUS
        if liste:
            return liste
        Domoticz.Error("Liste d'appareils invalide (attendu : numéros de 1 à 13 séparés par des virgules) : tous les appareils sont créés")
    return set(TOUS)


def creer_appareils(voulus=None):
    voulus = voulus if voulus is not None else set(TOUS)

    def a_creer(unite):
        return unite in voulus and unite not in Devices

    if a_creer(U_DISTRIBUER):
        Domoticz.Device(Name="Distribuer", Unit=U_DISTRIBUER, Type=244, Subtype=73, Switchtype=9, Used=1).Create()
    if a_creer(U_ETAT):
        Domoticz.Device(Name="État", Unit=U_ETAT, TypeName="Text", Used=1).Create()
    if a_creer(U_BATTERIE):
        Domoticz.Device(Name="Batterie", Unit=U_BATTERIE, TypeName="Percentage", Used=1).Create()
    if a_creer(U_PORTIONS):
        Domoticz.Device(Name="Portions distribuées", Unit=U_PORTIONS, TypeName="Custom",
                        Options={"Custom": "1;portions"}, Used=1).Create()
    if a_creer(U_CHARGE):
        Domoticz.Device(Name="En charge", Unit=U_CHARGE, Type=244, Subtype=73, Switchtype=0, Used=1).Create()
    if a_creer(U_DERNIERE):
        Domoticz.Device(Name="Dernière distribution", Unit=U_DERNIERE, TypeName="Text", Used=1).Create()
    if a_creer(U_PROGRAMME):
        Domoticz.Device(Name="Programme", Unit=U_PROGRAMME, TypeName="Text", Used=1).Create()
    if a_creer(U_CHOIX):
        Domoticz.Device(
            Name="Portions", Unit=U_CHOIX, Type=244, Subtype=62, Switchtype=18, Used=1,
            Options={"LevelActions": "|" * 10, "LevelNames": "Off|1|2|3|4|5|6|7|8|9|10",
                     "LevelOffHidden": "true", "SelectorStyle": "1"},
        ).Create()
        Devices[U_CHOIX].Update(nValue=1, sValue="10")
    if a_creer(U_JOUR):
        Domoticz.Device(Name="Portions aujourd'hui", Unit=U_JOUR, TypeName="Custom",
                        Options={"Custom": "1;portions"}, Used=1).Create()
    if a_creer(U_ALERTE):
        Domoticz.Device(Name="Alerte distribution", Unit=U_ALERTE, TypeName="Alert", Used=1).Create()
    if a_creer(U_CONNEXION):
        Domoticz.Device(Name="Connecté", Unit=U_CONNEXION, Type=244, Subtype=73, Switchtype=0, Used=1).Create()
    if a_creer(U_INCONNU):
        Domoticz.Device(Name="Point 13 (inconnu)", Unit=U_INCONNU, TypeName="Text", Used=1).Create()
    if a_creer(U_PROCHAIN):
        Domoticz.Device(Name="Prochain repas", Unit=U_PROCHAIN, TypeName="Alert", Used=1).Create()


def maj(unite, nvalue, svalue):
    if unite in Devices:
        Devices[unite].Update(nValue=nvalue, sValue=svalue)


def journal(texte):
    global dernier_message
    if texte != dernier_message:
        Domoticz.Log(texte)
        dernier_message = texte


# ---------------------------------------------------------------- portions du jour

def verifier_jour(maintenant):
    """Remet le compteur à zéro quand la date change."""
    global total_jour, date_jour
    if date_jour != maintenant.date():
        date_jour = maintenant.date()
        total_jour = 0
        maj(U_JOUR, 0, "0")


def ajouter_portions(n, maintenant):
    global total_jour
    verifier_jour(maintenant)
    total_jour += n
    maj(U_JOUR, 0, str(total_jour))


def charger_total_jour(maintenant):
    """Reprend le total du jour après un redémarrage de Domoticz."""
    global total_jour, date_jour
    date_jour = maintenant.date()
    total_jour = 0
    if U_JOUR in Devices:
        try:
            if str(Devices[U_JOUR].LastUpdate)[:10] == maintenant.strftime("%Y-%m-%d"):
                total_jour = int(float(Devices[U_JOUR].sValue))
        except (ValueError, TypeError, AttributeError):
            total_jour = 0


# ---------------------------------------------------------------- distributions

def debut_distribution(maintenant):
    global evenement
    evenement = {"debut": maintenant, "portions": None}
    demarrages.append(maintenant)
    del demarrages[:-50]
    maj(U_DERNIERE, 0, maintenant.strftime("%d/%m/%Y %H:%M:%S"))


def fin_distribution(maintenant):
    global evenement
    if evenement is None:
        return
    if evenement["portions"] is not None:
        n, source = evenement["portions"], "rapport du distributeur"
    elif derniere_requete and (maintenant - derniere_requete[0]).total_seconds() < 120:
        n, source = derniere_requete[1], "demande depuis Domoticz"
    else:
        n, source = 1, "valeur par défaut"
    evenement = None
    ajouter_portions(n, maintenant)
    Domoticz.Log("Distribution détectée : %d portion(s) (%s)" % (n, source))


def appliquer(dps, initial=False, maintenant=None):
    maintenant = maintenant or datetime.now()
    verifier_jour(maintenant)
    if DP_BATTERIE in dps:
        maj(U_BATTERIE, int(dps[DP_BATTERIE]), str(dps[DP_BATTERIE]))
    if DP_CHARGE in dps:
        en_charge = bool(dps[DP_CHARGE])
        maj(U_CHARGE, 1 if en_charge else 0, "On" if en_charge else "Off")
    if DP_RAPPORT in dps:
        maj(U_PORTIONS, 0, str(dps[DP_RAPPORT]))
        if evenement is not None and not initial:
            evenement["portions"] = int(dps[DP_RAPPORT])
    if DP_ETAT in dps:
        etat = dps[DP_ETAT]
        maj(U_ETAT, 0, ETATS.get(etat, str(etat)))
        if not initial:
            if etat == "feeding" and evenement is None:
                debut_distribution(maintenant)
            elif etat in ("done", "standby") and evenement is not None:
                fin_distribution(maintenant)
    for dp, valeur in dps.items():
        if dp in DP_CONNUS or dp in (DP_ETAT, DP_BATTERIE, DP_CHARGE, DP_RAPPORT):
            continue
        if inconnus.get(dp) != valeur:
            inconnus[dp] = valeur
            Domoticz.Log("Point inconnu %s = %s" % (dp, valeur))
        if dp == DP_INCONNU:
            maj(U_INCONNU, 0, str(valeur))


# ---------------------------------------------------------------- programme et prochain repas

def prochain_repas(maintenant):
    """Renvoie (heure du repas, heure, minute, portions) du prochain repas actif, ou None."""
    candidats = []
    for jours, heure, minute, nb, actif in repas:
        if not actif or jours != 127:
            continue
        t = datetime.combine(maintenant.date(), heure_du_jour(heure, minute))
        if t <= maintenant:
            t += timedelta(days=1)
        candidats.append((t, heure, minute, nb))
    return min(candidats) if candidats else None


def texte_programme(maintenant):
    suivant = prochain_repas(maintenant)
    tous_les_jours = all(r[0] == 127 for r in repas)
    marque = False
    morceaux = []
    for jours, heure, minute, nb, actif in repas:
        texte = "%02d:%02d (%d)" % (heure, minute, nb)
        if not tous_les_jours:
            texte += " [jours %d]" % jours
        if not actif:
            texte += " désactivé"
        elif suivant and not marque and jours == 127 and (heure, minute) == (suivant[1], suivant[2]):
            texte = "▶ " + texte
            marque = True
        morceaux.append(texte)
    return " · ".join(morceaux)


def duree_texte(delta):
    minutes = max(1, -int(-delta.total_seconds() // 60))   # arrondi à la minute supérieure
    if minutes < 60:
        return "%d min" % minutes
    return "%d h %02d" % (minutes // 60, minutes % 60)


def etat_prochain(maintenant):
    """Renvoie (niveau de couleur, texte) pour l'appareil Prochain repas."""
    suivant = prochain_repas(maintenant)
    if suivant is None:
        return 0, "Aucun repas programmé"
    delai = suivant[0] - maintenant
    texte = "Prochain repas : %02d:%02d (%d portion%s) dans %s" % (
        suivant[1], suivant[2], suivant[3], "s" if suivant[3] > 1 else "", duree_texte(delai))
    if delai <= timedelta(minutes=15):
        niveau = 3      # orange
    elif delai <= timedelta(minutes=60):
        niveau = 2      # jaune
    else:
        niveau = 1      # vert
    return niveau, texte


def rafraichir_programme(maintenant):
    if not repas:
        return
    texte = texte_programme(maintenant)
    if affichage.get("programme") != texte:
        affichage["programme"] = texte
        maj(U_PROGRAMME, 0, texte)
    niveau, texte_prochain = etat_prochain(maintenant)
    if affichage.get("prochain") != (niveau, texte_prochain):
        affichage["prochain"] = (niveau, texte_prochain)
        maj(U_PROCHAIN, niveau, texte_prochain)


# ---------------------------------------------------------------- surveillance des repas

def alerte(niveau, texte):
    maj(U_ALERTE, niveau, texte)


def evaluer_repas(maintenant):
    """Signale un repas programmé qui n'a pas démarré. Reste muet quand on ne peut pas savoir."""
    if debut_plugin is None:
        return
    aujourdhui = maintenant.date()
    for cle in [k for k in repas_etat if k[0] != aujourdhui]:
        del repas_etat[cle]
    for jours, heure, minute, nb, actif in repas:
        if not actif or jours != 127:     # 127 = tous les jours, seul cas connu
            continue
        cle = (aujourdhui, heure, minute)
        if cle in repas_etat:
            continue
        nom = "%02d:%02d" % (heure, minute)
        heure_repas = datetime.combine(aujourdhui, heure_du_jour(heure, minute))
        debut_fenetre = heure_repas - timedelta(seconds=AVANCE_REPAS)
        fin_fenetre = heure_repas + timedelta(seconds=RETARD_REPAS)
        if any(debut_fenetre <= d <= fin_fenetre for d in demarrages):
            repas_etat[cle] = "ok"
            alerte(1, "Repas de %s distribué" % nom)
            continue
        if maintenant < fin_fenetre:
            continue
        if debut_plugin > debut_fenetre or (derniere_deconnexion and derniere_deconnexion >= debut_fenetre):
            repas_etat[cle] = "inconnu"
            Domoticz.Log("Repas de %s non vérifiable (plugin ou distributeur hors ligne à ce moment)" % nom)
            continue
        repas_etat[cle] = "manque"
        alerte(4, "Repas de %s manqué" % nom)
        Domoticz.Error("Repas de %s manqué : aucune distribution détectée" % nom)


# ---------------------------------------------------------------- cycle de vie du plugin

def onStart():
    global lien, portions, debut_plugin, connexion_affichee
    Domoticz.Heartbeat(5)
    if Parameters["Mode6"] == "1":
        Domoticz.Debugging(1)
    connexion_affichee = None
    creer_appareils(appareils_voulus())
    maintenant = datetime.now()
    debut_plugin = maintenant
    charger_total_jour(maintenant)
    try:
        portions = max(1, min(10, int(float(Devices[U_CHOIX].sValue)) // 10))
    except (KeyError, ValueError):
        portions = 1
    champs = (("Adresse IP", "Address"), ("Access ID", "Username"),
              ("Access Secret", "Password"), ("Device ID", "Mode1"))
    manquants = [nom for nom, cle in champs if not str(Parameters.get(cle, "")).strip()]
    if manquants:
        Domoticz.Error("Réglages manquants : " + ", ".join(manquants))
        return
    lien = Lien({
        "ip": Parameters["Address"].strip(),
        "access_id": Parameters["Username"].strip(),
        "access_secret": Parameters["Password"].strip(),
        "device_id": Parameters["Mode1"].strip(),
        "region": Parameters["Mode2"],
        "version": Parameters["Mode3"],
        "debug": Parameters["Mode6"] == "1",
    })
    lien.start()


def onStop():
    global lien
    if lien is not None:
        lien.arreter()
        lien.join(timeout=5)
        lien = None


def onCommand(Unit, Command, Level, Color):
    global derniere_demande, portions, derniere_requete
    if Unit == U_CHOIX:
        portions = max(1, min(10, int(Level) // 10))
        maj(U_CHOIX, 1, str(portions * 10))
        Domoticz.Log("Portions par distribution : %d" % portions)
        return
    if Unit != U_DISTRIBUER:
        return
    if lien is None or not lien.connecte.is_set():
        Domoticz.Error("Distributeur non connecté : distribution annulée")
        return
    if time.time() - derniere_demande < 20:
        Domoticz.Error("Distribution ignorée : une demande a déjà été envoyée il y a moins de 20 secondes")
        return
    derniere_demande = time.time()
    if lien.envoyer({"c": "manuel", "n": portions}):
        derniere_requete = (datetime.now(), portions)
        Domoticz.Log("Distribution de %d portion(s) demandée" % portions)
    else:
        Domoticz.Error("Distribution impossible : le processus tinytuya ne répond pas")


def onHeartbeat():
    global repas, derniere_deconnexion, connexion_affichee, evenement
    if lien is None:
        return
    maintenant = datetime.now()
    while True:
        try:
            genre, valeur = lien.sortie.get_nowait()
        except queue.Empty:
            break
        if genre == "dps":
            appliquer(valeur[0], initial=valeur[1], maintenant=maintenant)
        elif genre == "plan":
            repas = valeur[1]
            if not repas:
                maj(U_PROGRAMME, 0, valeur[0])
            affichage.clear()
        elif genre == "log":
            journal(valeur)
        elif genre == "erreur":
            Domoticz.Error(valeur)
    connecte = lien.connecte.is_set()
    if not connecte:
        derniere_deconnexion = maintenant
    if connecte != connexion_affichee:
        connexion_affichee = connecte
        maj(U_CONNEXION, 1 if connecte else 0, "On" if connecte else "Off")
        if not connecte:
            maj(U_ETAT, 0, "Déconnecté depuis %s" % maintenant.strftime("%H:%M"))
    if evenement is not None and (maintenant - evenement["debut"]).total_seconds() > DUREE_MAX_DISTRIBUTION:
        fin_distribution(maintenant)
    verifier_jour(maintenant)
    rafraichir_programme(maintenant)
    evaluer_repas(maintenant)
