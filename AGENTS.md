# Hearthstone Replay Analyzer — règles de travail

Ce fichier est la mémoire exécutable du projet. Le relire intégralement au début de
chaque étape d’implémentation et le maintenir cohérent avec le comportement réel.

## Mission V1

Transformer un replay HSReplay XML local ou distant en deux exports factuels,
anonymisés et adaptés à un humain ou à un LLM :

1. charger et valider le replay non fiable ;
2. le parser prioritairement avec `python-hsreplay` ;
3. résoudre tous les Card IDs avec le `cards.json` frFR complet de
   HearthstoneJSON, cache compris ;
4. reconstruire prudemment deck, mulligan, chronologie, états connus, options et
   provenance des cartes lorsque le protocole le permet ;
5. anonymiser ;
6. produire `output/<game-id>/game_summary.md` et `game_analysis.json`.

La V1 est un extracteur factuel. Elle ne juge jamais un choix et ne recommande
jamais de ligne de jeu.

## Français obligatoire

- La locale par défaut est toujours `frFR`.
- Tout texte Hearthstone ou message visible par l’utilisateur est en français :
  cartes, textes, classes, actions, titres, CLI, erreurs et avertissements.
- Les clés techniques stables du JSON et les identifiants Python peuvent rester en
  anglais.
- Aucun fallback anglais silencieux. Une carte non résolue devient
  `Carte inconnue [CARD_ID]` et génère un avertissement.
- Le fallback anglais n’est permis que par une option explicite, désactivée par
  défaut.

## Règle anti-hallucination

- N’émettre que ce qui est explicite dans le replay, reconstruit par les outils
  HearthSim, ou résolu dans les données officielles HearthstoneJSON.
- Ne jamais déduire rétroactivement une carte adverse cachée parce que son identité
  est révélée plus tard.
- Distinguer `known` et `hidden` et, lorsque nécessaire, les sources
  `replay_explicit`, `gamestate_reconstructed` et `uncertain`.
- Préférer `Carte inconnue`, `Événement non classifié`, `Mulligan partiellement
  reconstruit` ou `État non disponible` à une affirmation incertaine.
- Conserver en JSON les détails techniques utiles au diagnostic, sans secret ni
  donnée personnelle.
- Ne pas supposer la sémantique de `Choices`, `SendChoices`, `ChosenEntities`,
  `Options` ou `SendOption` : la vérifier dans la spécification, les API et les
  fixtures réelles. Les options sont seulement celles enregistrées par le client,
  jamais toutes les lignes stratégiques possibles.

## Sources et architecture

- Parser en priorité avec `HSReplayDocument.from_xml_file()` puis
  `to_packet_tree()` ; ne pas créer un parseur HSReplay complet maison.
- Utiliser `hslog`/`python-hearthstone` pour les paquets, les états et les enums
  (`GameTag`, `Zone`, `BlockType`, etc.), sans nombres magiques.
- Une lecture XML complémentaire est admise uniquement pour une donnée mal exposée
  par l’API haut niveau ; elle doit être ciblée, documentée et testée.
- Utiliser `https://api.hearthstonejson.com/v1/latest/frFR/cards.json`, jamais le
  seul fichier collectible. Indexer par `id`, nettoyer le HTML des textes et garder
  aussi héros, pouvoirs, jetons et enchantements.
- Séparer strictement I/O, parsing, état, modèles, confidentialité et rendus.

## Sécurité et confidentialité

- Considérer tout fichier et toute réponse HTTP comme non fiables.
- Limiter la taille locale/distante (50 MiB par défaut), accepter uniquement
  HTTP/HTTPS, streamer, appliquer un timeout, vérifier les statuts et nettoyer les
  temporaires.
- Refuser XML manifestement invalide, DTD/entités externes et contenu non HSReplay ;
  ne permettre ni résolution externe, ni lecture de fichier arbitraire, ni path
  traversal.
- Ne jamais logger, stocker ou restituer une URL signée complète. Les logs ne
  montrent que l’hôte sans query string, même en DEBUG.
- `anonymize = true` par défaut. Les sorties partageables emploient `JOUEUR` et
  `ADVERSAIRE`.
- Ne jamais écrire BattleTag, nom de compte, `accountHi`, `accountLo`, credential ou
  signature dans `game_analysis.json`.
- Respecter le point de vue informationnel du joueur : main adverse inconnue par
  défaut, sauf révélation explicite au moment concerné.

## Cache et configuration

- Valeurs par défaut : `locale=frFR`, `anonymize=true`, téléchargement maximal
  50 MiB, timeout HTTP 20 s, sortie `output`, cache `.cache`.
- Cache attendu : `.cache/hearthstonejson/frFR/cards.json` et `metadata.json`.
- Utiliser immédiatement un cache valide. Si l’actualisation échoue, conserver le
  cache existant. Sans réseau et sans cache, produire une erreur claire en français.
- Ne jamais committer `.cache/` ni `output/`.

## Hors scope V1

Ne pas développer maintenant : scraping d’une page `hsreplay.net/replay/...`,
connexion/OAuth HSReplay, analyse de masse ou live de `Power.log`, GUI, application
web, IA, notation des plays, moteur de meilleur choix, simulation alternative ou
prédiction de la main adverse. Préserver seulement des frontières d’architecture
compatibles avec ces évolutions.

## Definition of done V1

La V1 n’est terminée que si tous les points suivants sont vrais :

- installation Python >= 3.11 et usage documentés en français ;
- CLI française interactive et commandes `analyser`, `inspecter`,
  `actualiser-cartes`, `configuration` utilisables ;
- replay local et URL XML directe validés et analysés en sécurité ;
- HearthstoneJSON frFR complet et cache/offline fonctionnels ;
- joueurs/classes, deck français, mulligan prudent, tours, actions principales,
  états main/board connus, options et effets spéciaux exposés autant que les données
  le permettent ;
- cartes générées reliées à leur source lorsqu’elle est explicite ;
- information cachée respectée et anonymisation testée ;
- Markdown lisible et JSON déterministe avec `schema_version: "1.0"` ;
- diagnostics disponibles et avertissements honnêtes pour toute limite ;
- tests unitaires, sécurité, français, confidentialité et bout en bout réussis ;
- `pytest` et `ruff` réussissent ;
- `samples/sample_replay.hsreplay` est analysé sans jamais être modifié, puis le
  Markdown généré est relu manuellement ;
- README et ce fichier décrivent fidèlement les capacités et limites réelles ;
- aucune information n’est inventée pour masquer une fonctionnalité incomplète.

## Discipline de livraison

- Relire ce fichier au début de chaque étape C à F.
- Garder les commits incrémentaux demandés et ne jamais committer avec des tests en
  échec.
- Préserver tous les fichiers utilisateur et échantillons existants.

## État réel de la V1

- Les quatre commandes, le menu interactif, les entrées locales ou HTTP(S), le
  cache frFR, le parseur intégré et les deux exports sont opérationnels.
- `game_analysis.json` suit le schéma `1.0` et `turns` contient des demi-tours ; le
  nombre de tours complets reste disponible dans `game.turn_count`.
- Les rapports sont écrits atomiquement dans un sous-dossier de partie neutralisé,
  après une dernière garde de confidentialité. Un même `game_id` remplace les deux
  rapports précédents de cette partie.
- Le Markdown masque les enchantements techniques invisibles pour rester compact ;
  le JSON conserve les faits techniques complets disponibles dans le modèle.
- Les limites assumées restent celles du protocole : options non stratégiques,
  adversaire caché non deviné, mulligan parfois partiel, événements complexes
  éventuellement non classifiés et absence de simulation ou de jugement des plays.
- Les replays fournis dans `samples/` sont des données utilisateur locales : ils
  servent à la validation réelle, restent inchangés et ne sont jamais commités.
