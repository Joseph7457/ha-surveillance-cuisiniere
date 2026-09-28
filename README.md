# Surveillance de cuisinière pour Home Assistant

**Être prévenu quand un feu reste allumé alors que personne n'est dans la cuisine.**

Un add-on Home Assistant qui regarde les boutons de la cuisinière avec une caméra ordinaire (testé avec une Tapo C220),
reconnaît quels feux sont allumés grâce à de simples pastilles blanches collées sur les boutons, détecte si quelqu'un
est présent dans la pièce, et permet d'envoyer une notification puis une alerte critique aux téléphones de la famille.

> 🇬🇧 *Home Assistant add-on that watches stove knobs through a regular camera (white reflective dots on the knobs),
> detects presence in the kitchen and lets you alert your family when a burner is left on with nobody around.
> Works day and night (infrared). Documentation is in French; issues in English are welcome.*

> ⚠️ **Ce n'est pas un dispositif de sécurité certifié.** C'est une aide et un système d'alerte.
> Il ne remplace ni un détecteur de fumée (obligatoire en Belgique), ni une sécurité de cuisinière certifiée
> (norme EN 50615). Voir [Limites](#limites).

---

## Pourquoi ce projet ?

Oublier une casserole sur le feu arrive à tout le monde, mais c'est un vrai danger pour les personnes distraites,
âgées, avec des troubles de la mémoire ou de l'attention. Les sécurités de cuisinière du commerce existent, mais elles
demandent souvent un électricien et ne préviennent pas les proches à distance.

Ce projet vise l'inverse : **s'installer soi-même, sans toucher à l'électricité, fonctionner sur n'importe quelle
cuisinière (vitrocéramique, induction, gaz…) et prévenir les bonnes personnes au bon moment.**

## Comment ça marche

1. **Une pastille blanche** (autocollant rétroréfléchissant) est collée au bout de chaque bouton.
   En position OFF, elle se trouve dans une « zone OFF » que l'on dessine une fois pour toutes.
2. **Deux pastilles de repère** collées à côté des boutons permettent de **recaler automatiquement** les zones
   si la caméra bouge un peu, et de savoir quand la caméra regarde bien les boutons.
3. L'add-on analyse le flux vidéo (RTSP) plusieurs fois par seconde :
   pastille dans la zone OFF → **éteint** ; pastille ailleurs → **allumé** ; pastille cachée (main, casserole) →
   on garde le dernier état connu. Un **anti-rebond** évite les faux changements.
4. La détection se fait **par luminosité**, donc elle marche aussi **la nuit** quand la caméra passe en infrarouge.
5. Si la caméra est motorisée, elle peut **alterner** entre les boutons et la pièce : l'add-on détecte alors le
   **mouvement** dans la pièce pour savoir si quelqu'un est là.
6. Tout est publié dans Home Assistant (via MQTT) : il ne reste qu'à écrire l'automatisation d'alerte
   (des exemples complets sont fournis).

## Ce qu'il faut

| Élément | Remarque |
|---|---|
| Home Assistant OS (ou Supervised) | Testé sur Raspberry Pi 5 |
| Une caméra avec flux RTSP | Testé avec Tapo C220 (motorisée). Une caméra fixe suffit si la présence est détectée autrement |
| Module **Mosquitto broker** | Obligatoire (communication MQTT) |
| 6 pastilles blanches rétroréfléchissantes | 4 pour les boutons, 2 repères |
| App Home Assistant sur les téléphones | Pour les notifications |

## Installation

1. Dans Home Assistant : **Paramètres → Modules complémentaires → Boutique → ⋮ → Dépôts**, ajoutez :
   ```
   https://github.com/Joseph7457/ha-surveillance-cuisiniere
   ```
2. Installez **Surveillance cuisinière** (la première installation compile l'image : 10–20 min sur un Raspberry Pi).
3. Onglet **Configuration** : adresse IP de la caméra, utilisateur et mot de passe du **compte caméra**
   (pour une Tapo : app Tapo → Paramètres avancés → Compte de la caméra). Préférez un mot de passe sans caractères spéciaux.
4. Onglet **Info** : activez **Afficher dans la barre latérale**, puis **Démarrer**.
   Le journal doit afficher `Flux vidéo connecté (stream1)`.

## Mise en place

1. **Collez les pastilles**, boutons sur OFF. Nettoyez d'abord à l'alcool.
2. **Collez deux pastilles de repère** sur une partie fixe et froide, visible en même temps que les boutons
   (par exemple à gauche et à droite de la rangée de boutons).
   *Un repère imprimé ne convient pas : l'encre jet d'encre est invisible en infrarouge.*
3. Ouvrez **Cuisinière** dans la barre latérale → **Dessiner les zones** (cochez « Agrandir l'image ») :
   un clic sur chaque repère, puis pour chaque bouton un rectangle autour du bouton entier et un petit rectangle
   autour de sa pastille en position OFF. **Enregistrer** vérifie tout sur l'image réelle.
4. Onglet **Direct** : tournez un bouton, il doit passer à « allumé ». Faites aussi le test dans le noir.

## Entités créées

| Entité | Rôle |
|---|---|
| `binary_sensor.surveillance_cuisiniere_un_feu_allume` | Au moins un feu allumé |
| `binary_sensor.surveillance_cuisiniere_feu_1` … `_feu_N` | État retenu de chaque feu |
| `sensor.surveillance_cuisiniere_feu_N_vision` | Ce que la caméra voit à l'instant (diagnostic) |
| `binary_sensor.surveillance_cuisiniere_presence_cuisine` | Mouvement dans la pièce (maintenu 90 s) |
| `sensor.surveillance_cuisiniere_dernier_mouvement` | Heure du dernier mouvement |
| `sensor.surveillance_cuisiniere_vue_camera` | `boutons` / `ailleurs` |
| `sensor.surveillance_cuisiniere_derniere_vue_des_boutons` | Dernière fois que les boutons ont été vus |
| `camera.surveillance_cuisiniere_image_des_boutons` | Image annotée, à joindre aux notifications |

Les noms exacts peuvent varier selon votre version de Home Assistant : vérifiez-les dans
**Paramètres → Appareils et services → MQTT → Surveillance cuisinière**.

## Automatisations d'exemple

Dans le dossier [`exemples/`](exemples/) :

- [`script_notifier_famille.yaml`](exemples/script_notifier_famille.yaml) — envoie une notification (avec photo et
  bouton « C'est bon, j'ai éteint ») à une liste de téléphones ; version critique pour Android.
- [`automatisation_alternance_camera.yaml`](exemples/automatisation_alternance_camera.yaml) — quand un feu est
  allumé, la caméra alterne entre les positions « boutons » (15 s) et « piece » (45 s).
- [`automatisation_alerte_feu.yaml`](exemples/automatisation_alerte_feu.yaml) — feu allumé et personne depuis
  10 min → notification ; sans réaction 10 min plus tard → alerte critique répétée.

Collez-les via **Paramètres → Automatisations et scènes → Créer → ⋮ → Modifier en YAML**
(le script dans l'onglet *Scripts*, les automatisations dans l'onglet *Automatisations*),
après avoir remplacé les noms en MAJUSCULES.

## Réglages

| Option | Défaut | Rôle |
|---|---|---|
| `flux` | `stream1` | Flux HD conseillé (les boutons sont petits dans l'image) |
| `images_par_seconde` | 3 | Fréquence d'analyse |
| `anti_rebond_images` | 5 | Images identiques avant de valider un changement |
| `seuil_luminosite` / `contraste_min` | 150 / 60 | À baisser si une pastille n'est pas trouvée, à monter en cas de faux reflets |
| `rayon_recherche_repere` | 0.06 | Décalage maximal toléré des repères (fraction de la largeur d'image) |
| `mouvement_seuil_pct` | 0.3 | Sensibilité de la détection de mouvement (plus bas = plus sensible) |
| `presence_maintien_secondes` | 90 | Durée pendant laquelle la présence reste active après un mouvement |

## Dépannage

- **`401 Unauthorized` dans le journal** : identifiants du compte caméra refusés. Les caméras Tapo se bloquent
  temporairement après plusieurs échecs, et n'acceptent que 2 usages simultanés parmi cloud / carte SD / RTSP.
  Redémarrez la caméra, désactivez l'enregistrement sur carte SD, vérifiez que la *compatibilité tierce* est activée.
- **Ne désinstallez pas l'add-on pour le mettre à jour** : cela efface la configuration et les zones.
- **Présence détectée alors que la pièce est vide** : la vue « pièce » ne doit montrer ni la cuisinière,
  ni une télé, ni une fenêtre avec des arbres. Augmentez `mouvement_seuil_pct` si besoin.
- **Un feu reste « caché »** : la pastille est masquée ou trop petite dans l'image ; utilisez `stream1`,
  rapprochez le cadrage, redessinez les zones.
- **L'automatisation ne se déclenche pas** : enregistrer une automatisation ou redémarrer Home Assistant remet à zéro
  les compteurs « depuis 10 minutes ».

## Limites

- La détection de mouvement ne voit pas une personne **immobile** (assise, qui lit…) : fausses alertes possibles.
  Un radar mmWave (LD2410 sur ESP32) règle ce problème.
- Pendant que la caméra regarde la pièce, l'extinction d'un feu est constatée au passage suivant sur les boutons.
- Le système dépend du réseau, de la caméra et de Home Assistant : s'ils tombent en panne, il n'y a pas d'alerte.
- Une caméra dans une cuisine pose des questions de vie privée : informez et obtenez l'accord des personnes concernées.

## Feuille de route

- **Version sans caméra** : un boîtier ESP32 avec capteur thermique et radar de présence, moins cher,
  plus respectueux de la vie privée, pensé pour les personnes âgées et leurs aidants.
- Traduction anglaise de la documentation.

Idées, retours d'expérience et contributions bienvenus via les *Issues*.

## Licence

[AGPL-3.0](LICENSE) — vous pouvez utiliser, modifier et redistribuer ce projet ; les versions modifiées
redistribuées, **y compris proposées comme service en ligne**, doivent rester sous la même licence et publier leur code.
