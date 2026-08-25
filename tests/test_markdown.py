from hscoach.models import (
    ActionType,
    BoardState,
    CardRef,
    DeckCard,
    GameAction,
    GameAnalysis,
    HeroState,
    MinionState,
    Mulligan,
    Player,
    PlayerSide,
    ReplayMetadata,
    SideState,
    TurnState,
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
        mulligan=Mulligan(offered=[maiev], kept=[maiev]),
        turns=[
            TurnState(
                turn_number=7,
                round_number=4,
                active_player=PlayerSide.PLAYER,
                start_state=start,
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
                end_state=end,
            )
        ],
    )


def test_markdown_contains_french_sections_and_current_stats() -> None:
    report = render_markdown(analysis_fixture())

    assert "# Replay Hearthstone" in report
    assert "Joueur : Chaman" in report
    assert "Adversaire : Paladin" in report
    assert "## Tour 4 — JOUEUR" in report
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
    assert report.endswith("\n")


def test_markdown_export_uses_safe_game_directory(tmp_path) -> None:
    analysis = analysis_fixture()
    analysis.metadata.game_id = "../../ailleurs"

    destination = export_markdown(analysis, tmp_path)

    assert destination.parent.parent == tmp_path.resolve()
    assert destination.name == "game_summary.md"
    assert destination.read_text(encoding="utf-8").startswith("# Replay Hearthstone")
    assert not (tmp_path.parent / "ailleurs").exists()
