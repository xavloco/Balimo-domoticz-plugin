# Plugin Domoticz pour distributeur de croquettes Balimo (Tuya)

Plugin [Domoticz](https://www.domoticz.com/) pour les distributeurs de croquettes connectés **Balimo** (appareils Tuya). Il communique avec le distributeur **en local** (réseau Wi-Fi/LAN) grâce à [tinytuya](https://github.com/jasonacox/tinytuya). Le cloud Tuya ne sert qu'à récupérer la clé locale et à lire le programme des repas.

> Projet non officiel, sans lien avec Balimo ni Tuya. Testé sur un seul modèle de distributeur Balimo (protocole local 3.4). Le bouton **Distribuer** lance une vraie distribution de croquettes.

## Fonctionnalités

| N° | Appareil Domoticz | Rôle |
|----|-------------------|------|
| 1 | Distribuer | Bouton : distribution manuelle du nombre de portions choisi |
| 2 | État | Au repos, distribution en cours, terminé, ou « Déconnecté depuis HH:MM » |
| 3 | Batterie | Niveau en % |
| 4 | Portions distribuées | Portions du dernier rapport du distributeur |
| 5 | En charge | Actif quand le distributeur charge |
| 6 | Dernière distribution | Date et heure du dernier démarrage |
| 7 | Programme | Repas programmés, avec ▶ devant le prochain |
| 8 | Portions | Sélecteur de 1 à 10 portions pour le bouton Distribuer |
| 9 | Portions aujourd'hui | Total depuis minuit (historique Domoticz) |
| 10 | Alerte distribution | Vert quand un repas démarre, rouge s'il n'a pas démarré 5 minutes après son heure |
| 11 | Connecté | Actif quand le plugin est connecté au distributeur |
| 12 | Point 13 (inconnu) | Valeur brute d'une fonction non documentée |
| 13 | Prochain repas | Heure et temps restant, en couleur : vert (> 1 h), jaune (< 1 h), orange (< 15 min) |

Autres points :

- Détection automatique de la version du protocole (3.3, 3.4 ou 3.5).
- Le dialogue avec le distributeur tourne dans un **processus Python séparé** (`helper.py`) : il libère le distributeur quand Domoticz s'arrête, et modifier les réglages du matériel ne demande pas de redémarrer Domoticz.
- Protection du bouton Distribuer : refusé si le distributeur n'est pas connecté, anti-rebond de 20 secondes, aucune commande n'est mise en attente pendant une coupure.
- Alerte de repas manqué : elle reste muette quand elle ne peut pas savoir (plugin ou distributeur hors ligne autour de l'heure du repas). Seuls les repas programmés **tous les jours** sont surveillés.
- Choix des appareils créés (Tous, Essentiel, Personnalisé).

## Prérequis

- Domoticz avec les plugins Python activés (testé avec Domoticz 2026.4 et Python 3.13 sur Raspberry Pi).
- `python3-pip` (le plugin installe lui-même tinytuya dans son dossier au premier démarrage).
- Le distributeur et Domoticz sur le **même réseau** (le Wi-Fi du distributeur est en 2.4 GHz).
- Un compte développeur Tuya, pour récupérer la clé locale et lire le programme.

## 1. Compte développeur Tuya (une seule fois)

1. Crée un compte sur <https://iot.tuya.com> (le site peut s'afficher en chinois : change la langue en haut à droite).
2. **Cloud > Development > Create Cloud Project** : *Industry* Smart Home, *Development Method* Smart Home, *Data Center* correspondant à ta région (Europe : **Central Europe**).
3. Autorise les services API proposés, dont **IoT Core**. Ce service gratuit peut demander un renouvellement périodique.
4. Dans le projet, onglet **Overview** : note l'**Access ID** et l'**Access Secret** (icône œil).
5. Onglet **Devices > Link App Account > Add App Account** : scanne le QR code avec l'appli Tuya / Smart Life. Le distributeur apparaît dans **All Devices**.
6. Relève son **Device ID** : colonne *Device ID* de la plateforme, ou dans l'appli (appareil, crayon, *Informations sur l'appareil*, *ID virtuel*).

Après un **reset** du distributeur, son Device ID change : refais l'étape 5 et relève le nouveau.

Réserve aussi son adresse IP dans ta box (bail DHCP statique).

## 2. Installation

Sur le Raspberry Pi de Domoticz (adapte le dossier des plugins) :

```bash
sudo apt install -y python3-pip git
cd /home/pi/domoticz/plugins
sudo git clone https://github.com/<ton-compte>/balimo-domoticz-plugin.git Balimo
sudo systemctl restart domoticz
```

Si Domoticz n'est pas un service (lancé à la main) :

```bash
sudo pkill domoticz; sleep 5; sudo /home/pi/domoticz/domoticz -daemon -www 8080 -sslwww 443
```

Dossiers de plugins courants : `/home/pi/domoticz/plugins`, `/opt/domoticz/plugins`, `/usr/share/domoticz/plugins`. Avec Docker, clone dans le dossier monté en volume pour les plugins.

Mise à jour :

```bash
cd /home/pi/domoticz/plugins/Balimo && sudo git pull && sudo systemctl restart domoticz
```

Au premier démarrage, tinytuya s'installe automatiquement dans le sous-dossier `lib/` du plugin. Si ça échoue (Journal : « Installation de tinytuya impossible ») :

```bash
sudo python3 -m pip install --upgrade --break-system-packages --target /home/pi/domoticz/plugins/Balimo/lib tinytuya
```

## 3. Ajouter le matériel dans Domoticz

**Réglages > Matériel**, type **Balimo - distributeur de croquettes (Tuya)**.

| Champ | À renseigner |
|-------|--------------|
| Adresse IP du distributeur | L'IP du distributeur sur ton réseau |
| Access ID (Tuya) | Celui de ton projet Tuya |
| Access Secret (Tuya) | Celui de ton projet Tuya (32 caractères) |
| Device ID | Celui du distributeur |
| Région Tuya | Europe centrale (`eu`) par défaut |
| Version du protocole | Automatique (recommandé) |
| Appareils à afficher | Tous, Essentiel (1, 2, 3, 8, 10, 11, 13) ou Personnalisé |
| Liste personnalisée | Avec *Personnalisé* : numéros séparés par des virgules, ex : `1,2,3,13` |
| Journal détaillé | Oui pour écrire `debug.log` dans le dossier du plugin |

Clique sur **Ajouter**, puis ouvre **Réglages > Journal** : tu dois voir « Clé locale récupérée depuis le cloud Tuya », « Protocole détecté : 3.4 » et « Connecté au distributeur ».

La clé locale est conservée dans `cle_locale.json` (droits `600`) et redemandée automatiquement si elle change. Les appareils déjà créés ne sont pas supprimés si tu réduis la liste : supprime-les à la main dans Domoticz.

## 4. Comment ça marche

Points de données (DP) Tuya utilisés par le distributeur testé :

| DP | Code Tuya | Usage |
|----|-----------|-------|
| 3 | `manual_feed` | Distribution manuelle (nombre de portions) |
| 4 | `feed_state` | `standby`, `feeding`, `done` |
| 10 | batterie | Pourcentage |
| 11 | charge | Booléen |
| 14 | `feed_report` | Portions de la dernière distribution |
| 13 | inconnu | Affiché brut |
| — | `meal_plan` | Programme des repas, lu **dans le cloud** toutes les heures (5 octets par repas : masque des jours, heure, minute, portions, actif) |

Comptage des portions : le plugin utilise d'abord le rapport du distributeur (DP 14). À défaut, il retient la dernière demande faite depuis Domoticz, sinon 1 portion. Le Journal écrit « Distribution détectée : N portion(s) (source) ».

## 5. Dépannage

Ouvre **Réglages > Journal** dans Domoticz.

| Message | Que faire |
|---------|-----------|
| `Installation de tinytuya impossible` | `sudo apt install python3-pip`, puis relance Domoticz |
| `appareil injoignable` | Vérifie l'IP et que le distributeur est sur le même réseau. Un seul programme à la fois peut être connecté au distributeur (ferme tout autre script tinytuya). Une version de protocole imposée et fausse donne l'erreur 901 : repasse sur Automatique |
| `Device ID introuvable dans ton compte Tuya` | Le distributeur n'est plus relié au projet : refais *Link App Account* |
| `cloud Tuya : ... sign invalid` | Access ID ou Access Secret faux, ou mauvaise région |
| `cloud Tuya : ... permission deny` | Distributeur non relié à ce projet, ou service IoT Core non autorisé |
| `Programme indisponible` | Le message qui suit donne la cause côté cloud. Le plugin réessaie toutes les heures |
| Rien dans la liste des types de matériel | Redémarre Domoticz et vérifie que les plugins Python sont activés |

Après un nouveau reset du distributeur : change le Device ID dans le matériel et supprime `cle_locale.json`.

Pour diagnostiquer la réponse du cloud, tinytuya peut être utilisé depuis `lib/` :

```python
import sys, json
sys.path.insert(0, "/home/pi/domoticz/plugins/Balimo/lib")
import tinytuya
c = tinytuya.Cloud(apiRegion="eu", apiKey="ACCESS_ID", apiSecret="ACCESS_SECRET", apiDeviceID="DEVICE_ID")
print(json.dumps(c.getstatus("DEVICE_ID"), indent=1))
```

## Limites connues

- Le programme n'est lisible que via le cloud Tuya (il n'est pas dans l'état local), et il est relu toutes les heures.
- Seuls les repas programmés tous les jours (masque 127) sont surveillés pour l'alerte et le « prochain repas ».
- Le distributeur n'accepte qu'un seul client local à la fois.
- Une commande acceptée par le distributeur n'est pas confirmée par un contrôle de l'état.

## Licence

MIT, voir [LICENSE](LICENSE).
