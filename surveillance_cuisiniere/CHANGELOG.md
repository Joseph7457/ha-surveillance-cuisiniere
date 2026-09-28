# Changelog

## 1.1.0
- Détection de présence par mouvement quand la caméra regarde la pièce
  (`binary_sensor.…_presence_cuisine`, `sensor.…_dernier_mouvement`).
- Nouvelles options `mouvement_seuil_pct` et `presence_maintien_secondes`.
- Les images identiques successives ne sont plus analysées deux fois.

## 1.0.1
- Tentatives de connexion au flux espacées progressivement (évite le blocage des caméras Tapo après des échecs).

## 1.0.0
- Première version : détection des pastilles jour/nuit, recalage sur deux repères, anti-rebond,
  pause automatique hors de la vue des boutons, interface de dessin des zones, entités MQTT.
