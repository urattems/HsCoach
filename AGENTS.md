# Hearthstone Replay Analyzer — règles de travail V3

Ce fichier est la mémoire exécutable du projet. Le relire intégralement avant toute
modification et le maintenir cohérent avec le comportement réellement testé.

## Mission

`hscoach` transforme un replay HSReplay XML local ou distant en exports factuels,
anonymisés et en français :

1. `game_summary.md`, destiné à une lecture humaine ;
2. `game_analysis.json`, document exhaustif au schéma `2.0` ;
3. `game_llm.json`, document compact au schéma indépendant `hscoach-llm/1.0`.

Le flux reste séparé : résolution de source, chargement sécurisé, parsing officiel,
résolution frFR, reconstruction factuelle, anonymisation, puis rendu. La V3 propose une
CLI et une GUI PySide6 qui appellent le même `AnalysisService`. Elle n'est ni un coach
stratégique, ni un moteur de simulation, ni un système de notation des plays.

## Invariant temporel prioritaire

Un tour du modèle est un demi-tour d'un seul côté. Ne jamais réduire de nouveau son
état à un couple ambigu début/fin. Les quatre frontières publiques sont :

| Champ | Frontière préférée | Sens |
|---|---|---|
| `turn_start_state` | `MAIN_READY` | avant la pioche et les triggers de début de tour suivants |
| `action_phase_start_state` | `MAIN_ACTION` | au moment où le joueur peut décider |
| `action_phase_end_state` | `MAIN_END` | après les actions, avant les triggers de fin |
| `turn_end_state` | `MAIN_CLEANUP` | après les triggers de fin |

Règles obligatoires :

- capturer un board complet uniquement aux frontières utiles, jamais après chaque
  micro-paquet ;
- permettre `MAIN_NEXT` comme repli de `MAIN_CLEANUP` avec un avertissement explicite ;
- permettre `FINAL_WRAPUP` comme fin seulement si `MAIN_END` a été observé ;
- ne pas traiter une concession depuis `MAIN_ACTION` comme une fin de tour normale ;
- conserver `None` et produire un avertissement si une frontière n'existe pas ;
- ne jamais remplacer un snapshot absent par un snapshot d'un autre instant ;
- garder les alias Python V1 `start_state` et `end_state` comme simples vues de
  `action_phase_start_state` et `action_phase_end_state`, pas comme champs JSON.

Tout changement de snapshots doit être testé avec une pioche entre `MAIN_READY` et
`MAIN_ACTION`, un buff de fin de tour entre `MAIN_END` et `MAIN_CLEANUP`, une frontière
absente et une concession.

## Deltas et chronologie

Le moteur de deltas compare les trois intervalles sémantiques du demi-tour. Il doit
conserver avant/après/différence, phase, séquence et niveau de complétude. Les types
publics sont `StateDelta`, `EntityDelta`, `HeroDelta`, `ManaDelta`, `ZoneDelta` et
`ValueDelta`.

- `complete` doit être faux si l'une des deux frontières comparées manque.
- Une source causale n'est attachée que lorsque le replay fournit explicitement source
  et cible. Une proximité dans le flux de paquets ne suffit pas.
- Les deltas reconstruits à partir des snapshots portent
  `gamestate_reconstructed`; les changements directs du protocole peuvent porter
  `replay_explicit`.
- Les séquences d'actions et de changements doivent être déterministes.
- Les métadonnées d'un événement non classifié doivent aider au diagnostic sans
  recopier le XML complet ni un secret.

La timeline reconnaît autant que les faits le permettent : pioche, carte jouée, sort,
arme, pouvoir héroïque, invocation, attaque, dégâts, soins, mort, buff, debuff,
silence, création, transformation, mélange, secret, fatigue et choix. Étendre cette
liste à partir d'une fixture prouvant la sémantique, jamais par supposition.

## Sémantique V3 : provenance, création et Dormant

Une relation `GameTag.CREATOR` décrit d'abord la provenance historique d'une entité.
Elle ne prouve pas qu'une création vient de se produire au paquet courant.

- Exporter la relation dans `provenance` avec l'identifiant de l'entité créatrice, son
  Card ID connu et une confiance factuelle.
- Ne produire `CARD_CREATED` que lorsqu'une entrée/création est réellement observée
  dans la timeline à cet instant.
- Une révélation tardive de `CREATOR` enrichit la provenance sans ajouter un faux
  événement tardif.
- Ne pas dire « créée par Carte inconnue » lorsqu'aucun événement de création n'est
  prouvé.

Le statut Dormant doit être explicite dans les états d'entité/minion. Utiliser les
`GameTag` réellement disponibles (`DORMANT`, `DORMANT_VISUAL` et transitions associées)
et les changements de zone, sans nombre magique.

- Classer `BECOMES_DORMANT` et `AWAKENS` seulement lorsque la transition est observée.
- Pendant Dormant, une représentation temporaire des statistiques ne doit pas devenir
  un faux debuff puis un faux buff.
- Conserver tout vrai changement de statistiques, y compris un buff réellement reçu
  avant ou pendant la période Dormante.
- À l'éveil, restaurer la dernière valeur gameplay prouvée, pas une valeur supposée.

Les tests minimaux couvrent provenance sans création instantanée, vraie création,
révélation tardive, passage Dormant, réveil, buff + Dormant et la régression Maiev si le
sample privé est présent.

## Événements gameplay et entités techniques

Le JSON exhaustif conserve les événements protocolaires. Les sorties gameplay peuvent
regrouper plusieurs occurrences seulement lorsqu'elles décrivent le même trigger : même
source entity, carte, type, cible éventuelle, fenêtre temporelle et données gameplay.
Conserver alors `protocol_occurrences` pour l'audit. Ne jamais fusionner deux triggers
réellement indépendants. En particulier, plusieurs paquets de démarrage décrivant
l'effet unique de Commandante Beatrix ne doivent pas produire « 2× » dans le résumé.

Les enchantements et helpers internes portent une classification `technical`. Ils
restent disponibles dans `game_analysis.json`, mais sont exclus par défaut du Markdown,
de `important_events` et du JSON LLM lorsqu'ils n'ajoutent aucune information utile.
Le delta gameplay qu'ils provoquent reste visible, même si l'entité technique ne l'est
pas.

## Mulligan, choix et options

Le statut du mulligan vaut `known`, `partial` ou `unknown`.

- `[]` signifie que la catégorie est connue et vide ; le Markdown affiche `Aucune.`.
- `None` signifie qu'elle n'est pas déterminée ; le Markdown affiche
  `Non déterminé.`.
- `partial` exige un avertissement expliquant ce qui ne concorde pas.
- Ne pas inverser conservées et renvoyées, ni déduire une catégorie d'un nom de paquet.

Pour `Choices`, `SendChoices` et `ChosenEntities`, conserver les offres et la réponse
enregistrée. Nommer un choix « Découverte » seulement si la mécanique `DISCOVER` de la
source est explicite. Sinon employer un type de choix générique ou non classifié.

Pour `Options` et `SendOption` :

- distinguer disponible, indisponible et sélectionnée ;
- conserver l'erreur protocolaire brute dans `game_analysis.json` ;
- rappeler que ces options ne représentent pas toutes les lignes stratégiques ;
- ne pas afficher `END_TURN error=INVALID` comme une option humaine significative dans
  le Markdown.

## Français et anti-hallucination

- La locale normale reste `frFR`.
- Tout texte visible par l'utilisateur est en français ; les clés JSON et identifiants
  Python peuvent rester en anglais.
- Aucun fallback anglais silencieux. Il doit rester lié à
  `--allow-en-fallback` et désactivé par défaut.
- Une carte introuvable devient `Carte inconnue [CARD_ID]` avec avertissement.
- Distinguer `known` et `hidden`, ainsi que `replay_explicit`,
  `gamestate_reconstructed` et `uncertain` lorsque nécessaire.
- Ne jamais révéler rétroactivement une carte cachée parce qu'elle apparaît plus tard.
- Ne jamais extrapoler le deck adverse, une causalité, un choix ou un résultat absent.
- Préférer `null`, une reconstruction partielle, un événement non classifié ou un
  avertissement à une information inventée.

## Parsing et sources de données

- Parser prioritairement avec `HSReplayDocument.from_xml_file()` puis
  `to_packet_tree()`.
- Utiliser `hslog` et `python-hearthstone` pour les paquets, l'état et les enums
  (`GameTag`, `Step`, `Zone`, `BlockType`, etc.) ; éviter les nombres magiques.
- Une lecture XML complémentaire doit être ciblée, documentée et couverte par un test,
  uniquement lorsque l'API haut niveau ne restitue pas correctement la donnée.
- Utiliser le `cards.json` complet de HearthstoneJSON, pas le seul sous-ensemble
  collectionnable. Conserver héros, pouvoirs, jetons et enchantements.
- Nettoyer le HTML des textes et résoudre les Card IDs de manière centralisée.

Les sources publiques sont des types explicites : `LocalReplaySource`,
`DirectXmlUrlSource`, `RawXmlSource` et `HsReplayPageSource`. La détection syntaxique
n'est pas le téléchargement. Elle doit notamment distinguer un chemin Windows d'une URL
et comparer le hostname normalisé, jamais une sous-chaîne vulnérable telle que
`hsreplay.net.example`.

- Une URL directe est téléchargée puis validée comme XML HSReplay ; un HTTP 200 HTML
  reste une erreur.
- Une URL signée n'est affichée que sous une forme sans query string et n'est jamais
  persistée.
- Une page `https://hsreplay.net/replay/<ID>` est reconnue mais non résolue en V3. Le
  message commun CLI/GUI explique les quatre étapes F12/Réseau pour copier l'URL XML
  directe, qui reste temporaire.
- Ne pas sonder d'endpoint privé, analyser le HTML, scraper le site ou contourner un
  refus HTTP. Le resolver retourne le message français documenté.
- Une future implémentation exige une API officielle/documentée ou une autorisation
  explicite, et reste confinée au resolver.

## Couche applicative commune

`AnalysisService` constitue la seule façade métier pour la CLI, la GUI et un futur
client machine. Il reçoit des `AnalysisRequest` et retourne des résultats par élément
ainsi qu'un bilan de lot. La GUI ne doit jamais appeler directement `parser.py`,
HearthstoneJSON, `gamestate.py` ou les renderers.

```text
CLI ─┐
     ├──> AnalysisService ──> moteur hscoach
GUI ─┘
```

- Continuer un lot après l'échec d'un élément.
- N'écrire que les formats explicitement demandés ; préserver le comportement
  historique de la CLI.
- Signaler une progression par étapes réelles, jamais par un pourcentage inventé.
- L'annulation empêche les travaux non commencés. Ne pas tuer brutalement un parseur
  ou interrompre une écriture atomique.
- Le service ne dépend d'aucune classe Qt et reste testable sans GUI.

## GUI PySide6

La GUI se lance avec `hscoach-gui` et `python -m hscoach.gui`. Elle doit rester simple,
française, redimensionnable et utilisable au clavier.

- Drag & drop et sélecteur acceptent un ou plusieurs replays, jamais une archive ou un
  `game_llm.json` comme entrée.
- Le travail réseau/parsing/export s'exécute hors du thread UI avec signaux Qt.
- Le bouton Analyser est désactivé sans source ou dossier de sortie valide.
- Par défaut : Markdown et JSON LLM actifs, JSON complet inactif.
- `QSettings` conserve seulement dossier de sortie, cases d'export, ouverture après
  analyse et éventuellement géométrie. Aucune source ou URL n'y est écrite.
- Le premier dossier proposé est `Documents/HSCoach`; le cache GUI utilise le dossier
  utilisateur renvoyé par `QStandardPaths.CacheLocation`.
- Les logs en mémoire et messages utilisateur sont redacted et sans traceback. Le mode
  diagnostic peut conserver une trace dans un emplacement utilisateur sûr.
- Tester la logique avec pytest-qt plutôt que des pixels, et valider manuellement les
  facteurs d'échelle Windows 125 % et 150 %.

## Contrats d'export et versionnage

`game_analysis.json` suit le schéma `2.0`. La racine conserve :

```text
schema_version, game, player, opponent, mulligan,
start_of_game_events, turns, important_events,
unresolved_cards, warnings, diagnostics
```

Le remplacement de `start_state`/`end_state` par quatre frontières est incompatible
avec le schéma V1. Il justifie `schema_version = "2.0"` et la version de paquet
`2.0.0`. Toute nouvelle rupture doit augmenter les deux versions concernées et ajouter
un test de contrat.

La RC historique par build est une évolution additive du paquet en `2.2.0`. Elle conserve les identifiants
de schéma `2.0` et `hscoach-llm/1.0` tant que leurs clés existantes gardent leur sens.
Les nouveaux champs de provenance, Dormant, classification technique et occurrences
protocolaires sont additifs ; la correction de `CARD_CREATED` retire une interprétation
fausse sans transformer le contrat structurel. Si une clé est supprimée, renommée ou
change de type, augmenter le schéma concerné avant la livraison.

`game_llm.json` suit son propre schéma `hscoach-llm/1.0`, porté par le champ
`schema_version`. Sa racine contient `game`, `cards`, `player_deck`, `mulligan`,
`start_of_game_events`, `turns`, `important_events` et `warnings`. Les données statiques
de carte sont centralisées dans `cards.definitions`, leurs occurrences dans
`cards.entities`, et les états dynamiques restent dans les tours. Les frontières après
le snapshot de décision peuvent référencer les `state_changes` nécessaires à leur
reconstruction ; `important_events` peut référencer des séquences déjà exportées. Ne
pas obtenir de compacité en supprimant une information stratégique factuelle, une
incertitude ou un avertissement.

Les trois rendus doivent être déterministes, UTF-8, vérifiés par la garde de
confidentialité et écrits atomiquement. Valider les trois représentations avant de
créer le premier fichier afin d'éviter une livraison partielle.

## Sécurité et confidentialité

Considérer tout fichier et toute réponse HTTP comme non fiables.

- Limite par défaut : 50 Mio ; timeout HTTP : 20 s ; HTTP/HTTPS uniquement ;
  téléchargement en streaming et temporaires nettoyés.
- Vérifier la racine HSReplay et refuser tout sous-ensemble DTD interne, toute
  déclaration d'entité et tout XML manifestement invalide avant le parseur officiel.
  Un DOCTYPE externe simple peut être conservé pour les exports HSReplay officiels,
  sans jamais télécharger ni résoudre le DTD distant.
- Ne permettre ni résolution externe, ni lecture arbitraire, ni path traversal.
- Ne jamais logger ou exporter une URL signée complète. Même en DEBUG, ne conserver
  que l'hôte sans query string.
- L'anonymisation reste toujours active dans la CLI : `JOUEUR`/`ADVERSAIRE` ou
  `PLAYER`/`OPPONENT`.
- Ne jamais exporter BattleTag, nom de compte, `accountHi`, `accountLo`, credential,
  token ou signature.
- Appliquer la détection de marqueurs sensibles, y compris les heuristiques d'URL
  signée, aux trois rapports.
- Respecter le point de vue informationnel du joueur dans chaque snapshot.
- Neutraliser le `game_id` et vérifier que tout chemin final reste sous le dossier de
  sortie autorisé.
- Appliquer la garde finale uniquement aux rapports demandés mais avant toute première
  écriture ; une option d'export désactivée ne doit créer aucun fichier.
- Ne jamais placer une query string, une source ou une identité dans `QSettings`, un
  tooltip, la liste GUI, le presse-papiers ou un log applicatif.
- Ne jamais exécuter, extraire ou importer dynamiquement un fichier déposé. La limite de
  taille s'applique aux entrées locales et distantes.

Les replays bruts restent sensibles même si les rapports sont anonymisés.

## Samples, fixtures et tests propres

- `samples/` contient exclusivement des données utilisateur locales et privées.
- `.gitignore` doit garder `/samples/*` et l'exception
  `!/samples/.gitkeep`.
- Ne jamais modifier, renommer, déplacer ou committer un replay réel fourni par
  l'utilisateur.
- Les tests nécessitant `samples/sample_replay.hsreplay` sont des intégrations
  optionnelles et doivent être ignorés proprement si le fichier manque.
- Toute régression essentielle doit aussi posséder une petite fixture anonyme,
  synthétique ou réduite, versionnable sans donnée personnelle.
- La suite principale doit réussir depuis un clone contenant uniquement les fichiers
  suivis par Git. Vérifier réellement ce cas avant livraison.
- Pour une intégration réelle, calculer l'empreinte du replay avant/après et confirmer
  qu'elle est inchangée.

Couverture minimale pour tout changement V2 : frontière temporelle concernée, delta ou
action, donnée cachée, sérialisation exhaustive, sérialisation compacte, Markdown et
confidentialité si applicable. Ne jamais rendre un test unitaire dépendant du cache
réseau ou d'un sample privé.

Commandes de validation :

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

## Cache et configuration

Valeurs par défaut : `locale=frFR`, anonymisation active, 50 Mio, 20 secondes,
sortie `output/`, cache `.cache/`.

Ces chemins relatifs restent ceux de la CLI pour compatibilité. La GUI injecte un
dossier initial `Documents/HSCoach` et un cache sous `QStandardPaths.CacheLocation`,
afin qu'une application extraite ou placée près de `Program Files` n'écrive jamais à
côté de son exécutable et ne demande aucune élévation.

Le cache attendu est :

```text
.cache/hearthstonejson/<build-ou-latest>/frFR/cards.json
.cache/hearthstonejson/<build-ou-latest>/frFR/metadata.json
```

`metadata.json` doit fournir une empreinte SHA-256 valide. La recalculer et la vérifier
avant tout usage de `cards.json`. Un mismatch, des métadonnées absentes/invalides ou un
fichier illisible rendent le cache corrompu. Tenter alors une actualisation ; hors
ligne et sans cache valide, retourner une erreur française claire. Ne conserver un
ancien cache après l'échec d'une actualisation que s'il avait été validé auparavant.
Écrire cartes et métadonnées atomiquement.

Ne jamais committer `.cache/` ni `output/`.

## CLI et diagnostics

Préserver les commandes `analyser`, `inspecter`, `actualiser-cartes`, `configuration`
et le menu français. Une analyse réussie doit annoncer les trois rapports. Une erreur
attendue ne doit apparaître qu'une fois en mode normal ; `--verbose` peut fournir une
trace de diagnostic supplémentaire sans exposer de secret.

Les handlers CLI passent par `AnalysisService`; ils ne reconstruisent pas un second
pipeline. La CLI conserve par défaut ses trois exports historiques, même si la GUI
propose Markdown + JSON LLM seulement. Une future commande machine doit réserver
`stdout` au JSON et envoyer les logs sur `stderr`.

Les diagnostics factuels couvrent classes joueur/adversaire, entités, événements,
demi-tours, actions, deltas, buffs, dégâts, soins, créations, options, cartes inconnues,
actions non classifiées, statut du mulligan et complétude des snapshots. Ne pas inventer
un score global de « qualité ».

## Démarrage de partie et Markdown

Distinguer les marqueurs protocolaires de début de partie des effets de gameplay.
Le JSON exhaustif peut garder des occurrences utiles au diagnostic. Le Markdown ne
doit fusionner que de vrais événements de gameplay identiques ; une répétition de
protocole ne devient pas un second effet.

Chaque demi-tour Markdown conserve les sections « Début du demi-tour », « Au moment de
décider », décisions, actions, changements observés, « Fin de la phase d'action » et
« Après les déclenchements de fin de tour ». Un état manquant doit être annoncé comme
indisponible, jamais remplacé silencieusement.

## Hors scope

Ne pas implémenter sans mission distincte : scraping d'une page publique
`hsreplay.net/replay/...`, OAuth HSReplay, analyse live ou massive de `Power.log`,
surveillance de dossiers, application web, moteur de meilleur choix, simulation,
notation de play ou prédiction de main adverse. Une URL HTTP(S) directe vers le XML
reste prise en charge. La V3 inclut une GUI desktop, mais pas de serveur local, de
service IA externe, d'overlay en jeu ni d'application Overwolf.

## Packaging Windows et publication

Le build utilisateur se fait avec PyInstaller en mode one-folder sur Windows et produit
`dist/HSCoach/HSCoach.exe`. PyInstaller n'est pas un cross-compilateur : ne jamais
présenter un bundle produit sur Linux/macOS comme une Release Windows testée.

- Le script `scripts/build_windows.ps1` utilise l'extra `.[gui,build]`, `--windowed`,
  `--onedir`, `--noupx` et ne demande pas l'élévation UAC.
- `hearthstone`, `hslog` et `hsreplay` importent encore `pkg_resources.require()` ; le
  build doit copier explicitement leurs métadonnées de distribution.
- Le smoke test crée réellement l'application Qt à partir du binaire final.
- Le bundle inclut `LICENSE` et les notices nécessaires aux dépendances redistribuées,
  notamment les textes GPLv3/LGPLv3 officiels requis pour l'option open source
  Qt/PySide6. Il n'inclut jamais samples, sorties ou cache.
- L'inclusion des textes ne certifie pas la conformité : tant que les DLL/plugins Qt,
  leurs composants tiers, leurs notices et l'accès aux sources correspondantes n'ont
  pas été audités pour le bundle exact, la publication publique du binaire est bloquée.
- L'artefact CI est optionnel, manuel et non signé ; ne pas l'activer avant ce gate.
- La Release est extraite dans un emplacement utilisateur. Sorties, cache et réglages
  ne sont jamais écrits dans `Program Files`.
- Un binaire non signé est annoncé comme tel ; signature de code et réputation
  SmartScreen restent une limite distincte.
- Construire une Release depuis un clone/une archive Git propre, jamais en zippant le
  dossier de travail contenant des fichiers ignorés.

Le dépôt public doit contenir une vraie licence MIT, un README factuel, une contribution
guide, une matrice de tests manuels et aucune donnée utilisateur dans toute l'histoire
Git pertinente. Ne pas inventer une URL de dépôt ou de Release si aucun remote n'est
configuré.

## Overwolf

Overwolf n'est plus une direction active. `docs/OVERWOLF.md` est une archive historique,
pas une roadmap. Ne créer aucune app, bridge ou dépendance Overwolf sans mission distincte.

## Definition of done V3

Une évolution V3 n'est livrable que si le socle V2 reste satisfait et si :

- les quatre frontières gardent leur sens et toute absence est honnêtement exposée ;
- les deltas sont objectifs, séquencés et sans attribution causale inventée ;
- actions, choix, options et mulligan conservent leurs incertitudes ;
- les informations cachées restent cachées à chaque instant ;
- les trois rapports sont déterministes, atomiques et passent la garde de
  confidentialité ;
- les schémas et la version du paquet correspondent au contrat documenté ;
- CREATOR est une provenance et `CARD_CREATED` exige un événement observé ;
- Dormant/réveil ne produisent pas de faux deltas de statistiques ;
- la déduplication gameplay conserve les occurrences protocolaires ;
- les entités techniques restent auditables sans polluer les sorties de coaching ;
- le cache est contrôlé par SHA-256 ;
- CLI et GUI appellent le même `AnalysisService` et le thread UI ne parse jamais ;
- fichiers locaux, lots, XML direct, erreurs, réglages et annulation sont testés ;
- une page HSReplay est refusée clairement sans scraping ;
- la suite fonctionne sans sample privé, puis les intégrations réelles disponibles
  sont exécutées sans modifier leurs fichiers ;
- `pytest`, `ruff check` et `ruff format --check` réussissent ;
- la GUI s'ouvre en développement et son smoke test termine ;
- le build propre produit et lance `dist/HSCoach/HSCoach.exe` sans Python ni admin ;
- la matrice `docs/MANUAL_TESTING.md` est exécutée dans la mesure permise par
  l'environnement ;
- README et AGENTS décrivent uniquement les capacités réellement présentes.

## Discipline de livraison

- Préserver toute modification ou donnée utilisateur sans rapport avec la tâche.
- Favoriser les changements ciblés et des commits incrémentaux avec tests verts.
- Ne jamais masquer une limite par un fallback silencieux ou une formulation plus
  assurée que les données.
- Pour un nouveau paquet protocolaire, vérifier l'enum/API installée et ajouter une
  fixture avant d'écrire une règle métier.
- Après un changement d'export, examiner un exemple rendu, sa taille, sa stabilité et
  sa confidentialité, pas seulement son type Python.
