from hscoach.models import (
    ActionType,
    BoardState,
    CardRef,
    Decision,
    DeckCard,
    EntityDelta,
    GameAction,
    GameAnalysis,
    HeroState,
    MinionState,
    Mulligan,
    Player,
    PlayerSide,
    RecordedOption,
    ReplayMetadata,
    SideState,
    TurnPhase,
    TurnState,
    ValueDelta,
    Visibility,
)
from hscoach.output.markdown import export_markdown, render_markdown


def analysis_fixture() -> GameAnalysis:
    maiev = CardRef(
        entity_id=12,
        card_id="JAIL_850",
        name="Maiev la Gardienne",
        cost=4,
        attack=1,
        health=3,
    )
    hidden = CardRef(
        entity_id=20,
        card_id=None,
        name="Carte inconnue",
        visibility=Visibility.HIDDEN,
    )
    technical_enchantment = CardRef(
        entity_id=21,
        card_id="TECH_001e",
        name="Cost - 2",
        card_type="ENCHANTMENT",
    )
    start = BoardState(
        player=SideState(
            side=PlayerSide.PLAYER,
            mana_available=4,
            mana_used=0,
            hero=HeroState(health=30),
            hand=[maiev],
        ),
        opponent=SideState(
            side=PlayerSide.OPPONENT,
            hero=HeroState(health=28, armor=2),
            hand=[hidden],
            hidden_hand_count=1,
        ),
    )
    end = BoardState(
        player=SideState(
            side=PlayerSide.PLAYER,
            mana_available=0,
            mana_used=4,
            hero=HeroState(health=30),
            board=[MinionState(card=maiev, attack=1, health=7, max_health=7)],
        ),
        opponent=start.opponent,
    )
    return GameAnalysis(
        metadata=ReplayMetadata(
            game_id="151665",
            result="Victoire",
            turn_count=4,
            duration_seconds=125,
            game_format="Standard",
            game_type="Partie classée",
        ),
        player=Player(
            side=PlayerSide.PLAYER,
            entity_id=2,
            player_id=1,
            card_class="Chaman",
            deck=[DeckCard(card=maiev, count=2)],
        ),
        opponent=Player(
            side=PlayerSide.OPPONENT,
            entity_id=3,
            player_id=2,
            card_class="Paladin",
        ),
        mulligan=Mulligan(offered=[maiev], kept=[maiev], returned=[], received=[]),
        turns=[
            TurnState(
                turn_number=7,
                round_number=4,
                active_player=PlayerSide.PLAYER,
                turn_start_state=start,
                action_phase_start_state=start,
                actions=[
                    GameAction(
                        sequence=1,
                        action_type=ActionType.PLAY_CARD,
                        player=PlayerSide.PLAYER,
                        description="JOUEUR joue Maiev la Gardienne.",
                    ),
                    GameAction(
                        sequence=2,
                        action_type=ActionType.CREATE_CARD,
                        player=PlayerSide.SYSTEM,
                        description="Cost - 2 est créée.",
                        target_card=technical_enchantment,
                    ),
                ],
                action_phase_end_state=end,
                turn_end_state=end,
            )
        ],
    )


def test_markdown_contains_french_sections_and_current_stats() -> None:
    report = render_markdown(analysis_fixture())

    assert "# Replay Hearthstone" in report
    assert "Joueur : Chaman" in report
    assert "Adversaire : Paladin" in report
    assert "## Tour 4 — JOUEUR" in report
    assert "### Au moment de décider" in report
    assert "### Après les déclenchements de fin de tour" in report
    assert "Maiev la Gardienne — 1/7" in report
    assert "1 carte inconnue" in report
    assert "Warden Maiev" not in report
    assert "Cost - 2" not in report


def test_markdown_groups_deck_and_states_limits() -> None:
    report = render_markdown(analysis_fixture())

    assert "### 4 mana" in report
    assert "2× Maiev la Gardienne" in report
    assert "Deck adverse complet : inconnu." in report
    assert "n’est pas extrapolé" in report
    assert "- Aucune." in report
    assert report.endswith("\n")


def test_markdown_export_uses_safe_game_directory(tmp_path) -> None:
    analysis = analysis_fixture()
    analysis.metadata.game_id = "../../ailleurs"

    destination = export_markdown(analysis, tmp_path)

    assert destination.parent.parent == tmp_path.resolve()
    assert destination.name == "game_summary.md"
    assert destination.read_text(encoding="utf-8").startswith("# Replay Hearthstone")
    assert not (tmp_path.parent / "ailleurs").exists()


def test_markdown_hides_invalid_end_turn_marker_and_labels_option_availability() -> None:
    analysis = analysis_fixture()
    analysis.turns[0].decisions = [
        Decision(
            sequence=1,
            timestamp=None,
            selected_option_index=2,
            options=[
                RecordedOption(
                    index=0,
                    option_type="Fin du tour",
                    description="Terminer le tour",
                    error="INVALID",
                    available=False,
                ),
                RecordedOption(
                    index=1,
                    option_type="Action",
                    description="Jouer une carte",
                    available=True,
                ),
                RecordedOption(
                    index=2,
                    option_type="Action",
                    description="Utiliser le pouvoir",
                    error="REQ_ENOUGH_MANA",
                    available=False,
                    selected=True,
                ),
            ],
        )
    ]

    report = render_markdown(analysis)

    assert "Action choisie : Utiliser le pouvoir" in report
    assert "Option disponible : Jouer une carte" in report
    assert "Terminer le tour" not in report
    assert "INVALID" not in report
    assert "REQ_ENOUGH_MANA" not in report


def test_markdown_deduplicates_protocol_only_beatrix_occurrence() -> None:
    analysis = analysis_fixture()
    analysis.start_of_game_events = _beatrix_start_events(protocol_only_second=True)

    report = render_markdown(analysis)

    description = "Commandante Beatrix déclenche son effet de début de partie."
    assert report.count(description) == 1
    assert f"2× {description}" not in report


def test_markdown_keeps_two_independent_beatrix_triggers() -> None:
    analysis = analysis_fixture()
    analysis.start_of_game_events = _beatrix_start_events(protocol_only_second=False)

    report = render_markdown(analysis)

    assert report.count("Commandante Beatrix déclenche son effet de début de partie.") == 2


def test_markdown_keeps_gameplay_delta_but_hides_technical_enchantment_name() -> None:
    analysis = analysis_fixture()
    maiev = analysis.player.deck[0].card
    technical = analysis.turns[0].actions[1].target_card
    assert technical is not None
    analysis.turns[0].entity_deltas = [
        EntityDelta(
            sequence=3,
            entity_id=maiev.entity_id or 12,
            side=PlayerSide.PLAYER,
            phase=TurnPhase.ACTION_PHASE_END,
            attribute="health",
            value=ValueDelta(before=3, after=5, delta=2),
            card=maiev,
            source_card=technical,
        )
    ]

    report = render_markdown(analysis)

    assert "points de vie : 3 → 5 (+2)" in report
    assert "Cost - 2" not in report


def _beatrix_start_events(*, protocol_only_second: bool) -> list[GameAction]:
    beatrix = CardRef(
        entity_id=70,
        card_id="BEATRIX",
        name="Commandante Beatrix",
        card_type="MINION",
    )
    description = "Commandante Beatrix déclenche son effet de début de partie."
    return [
        GameAction(
            sequence=1,
            action_type=ActionType.START_GAME_EFFECT,
            player=PlayerSide.PLAYER,
            description=description,
            source_card=beatrix,
            metadata={"protocol_only_reveal": False},
        ),
        GameAction(
            sequence=2,
            action_type=ActionType.START_GAME_EFFECT,
            player=PlayerSide.PLAYER,
            description=description,
            source_card=beatrix,
            metadata={"protocol_only_reveal": protocol_only_second},
        ),
    ]
