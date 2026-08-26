# Notices relatives aux composants tiers

HSCoach est distribué sous licence MIT, mais son bundle Windows contient des composants
tiers régis par leurs propres licences. Ce document est informatif ; les textes de
licence présents dans les métadonnées des distributions installées restent les textes
de référence.

## Interface et runtime

- **Python** — Python Software Foundation License. Le build recopie `LICENSE.txt` de
  l’interpréteur utilisé.
- **PySide6, Shiboken6 et Qt** — LGPL-3.0-only ou licences alternatives proposées par
  The Qt Company. Documentation : <https://doc.qt.io/qtforpython-6/licenses.html>.
  Sources Qt : <https://code.qt.io/>. Le bundle one-folder conserve les bibliothèques
  dynamiques séparées et les métadonnées de distribution PySide6.

Une redistribution fondée sur l’option LGPL doit préserver les droits accordés par la
LGPL, notamment l’accès au code source correspondant des bibliothèques et la possibilité
de les remplacer. Consultez les obligations officielles avant toute Release :
<https://www.qt.io/development/open-source-lgpl-obligations>.

Les copies verbatim de la GPLv3 et de la LGPLv3 provenant du dépôt officiel PySide sont
versionnées dans `licenses/` et recopiées dans le bundle. Cela ne suffit pas à certifier
une Release : les wheels installés peuvent ne fournir dans leur `.dist-info` qu’une
référence à la licence commerciale, malgré leurs métadonnées LGPL/GPL. Le responsable de
la distribution doit choisir la base de licence applicable, auditer les bibliothèques
Qt réellement embarquées, joindre leurs notices tierces pertinentes et maintenir un
accès conforme aux sources correspondantes. Une distribution commerciale suit à la
place les termes du contrat Qt concerné.

## Parsing et données Hearthstone

- **python-hearthstone** — MIT — <https://github.com/HearthSim/python-hearthstone>
- **python-hslog** — MIT — <https://github.com/HearthSim/python-hslog>
- **python-hsreplay** — MIT — <https://github.com/HearthSim/python-hsreplay>
- **HSReplay XML specification** — CC0 — <https://github.com/HearthSim/hsreplay-xml>

Le cache HearthstoneJSON est téléchargé sur la machine de l’utilisateur et n’est pas
embarqué dans l’archive de l’application.

## Réseau et dépendances Python

Le graphe installé inclut notamment HTTPX, HTTPCore, AnyIO, Requests, lxml, certifi,
idna, urllib3, charset-normalizer, h11 et aniso8601. Leurs licences sont recopiées depuis
les répertoires `.dist-info` par le pipeline de build lorsqu’elles sont présentes.

PyInstaller sert uniquement à produire le bundle. Son exception de licence autorise la
distribution de l’exécutable sous la licence de l’application, sous réserve des licences
des dépendances : <https://pyinstaller.org/en/stable/license.html>.

Ce relevé facilite l’audit mais ne constitue pas un avis juridique. La liste exacte des
composants et licences doit être vérifiée sur le bundle final de chaque Release.

## Marques

Hearthstone et les éléments associés sont des marques ou contenus de Blizzard
Entertainment. HSReplay.net est un service de HearthSim. HSCoach est un projet
communautaire indépendant, sans affiliation avec ces sociétés, et n’embarque aucun
asset graphique Blizzard ou HSReplay.net.
