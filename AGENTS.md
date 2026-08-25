# Hearthstone Replay Analyzer — règles de travail V2

Ce fichier est la mémoire exécutable du projet. Le relire intégralement avant toute
modification et le maintenir cohérent avec le comportement réellement testé.

## Mission

`hscoach` transforme un replay HSReplay XML local ou distant en trois exports
factuels, anonymisés et en français :

1. `game_summary.md`, destiné à une lecture humaine ;
2. `game_analysis.json`, document exhaustif au schéma `2.0` ;
3. `game_llm.json`, document compact au schéma indépendant `hscoach-llm/1.0`.

Le flux reste séparé : chargement sécurisé, parsing officiel, résolution frFR,
reconstruction factuelle, anonymisation, puis rendu. La V2 n'est ni un coach
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
- Vérifier la racine HSReplay et refuser DTD, entités externes et XML manifestement
  invalide avant le parseur officiel.
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

Le cache attendu est :

```text
.cache/hearthstonejson/frFR/cards.json
.cache/hearthstonejson/frFR/metadata.json
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
surveillance de dossiers, GUI, application web, moteur de meilleur choix, simulation,
notation de play ou prédiction de main adverse. Une URL HTTP(S) directe vers le XML
reste prise en charge.

## Definition of done V2

Une évolution V2 n'est livrable que si :

- les quatre frontières gardent leur sens et toute absence est honnêtement exposée ;
- les deltas sont objectifs, séquencés et sans attribution causale inventée ;
- actions, choix, options et mulligan conservent leurs incertitudes ;
- les informations cachées restent cachées à chaque instant ;
- les trois rapports sont déterministes, atomiques et passent la garde de
  confidentialité ;
- les schémas et la version du paquet correspondent au contrat documenté ;
- le cache est contrôlé par SHA-256 ;
- la suite fonctionne sans sample privé, puis les intégrations réelles disponibles
  sont exécutées sans modifier leurs fichiers ;
- `pytest`, `ruff check` et `ruff format --check` réussissent ;
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
