from hscoach.models import (
    BoardState,
    CardRef,
    HeroState,
    InformationSource,
    MinionState,
    PlayerSide,
    SideState,
    TurnPhase,
    TurnState,
)
from hscoach.replay.deltas import build_turn_state_deltas, compare_board_states


def test_compare_board_states_reports_visible_changes_without_inventing_a_source() -> None:
    moved = _card(10, "MOVED")
    changed = _card(20, "CHANGED")
    removed = _card(40, "REMOVED")
    added = _card(50, "ADDED")
    before = _board(
        hand=[moved, removed],
        board=[_minion(changed, attack=1, health=4, max_health=4)],
        hero=HeroState(health=30, armor=0, attack=0),
        mana_available=5,
        mana_used=0,
    )
    after = _board(
        hand=[added],
        board=[
            _minion(moved, attack=2, health=2, max_health=2),
            _minion(
                changed,
                attack=3,
                health=2,
                max_health=5,
                taunt=True,
                divine_shield=True,
                stealth=True,
                frozen=True,
                silenced=True,
            ),
        ],
        hero=HeroState(health=27, armor=2, attack=1),
        mana_available=2,
        mana_used=3,
    )

    delta, next_sequence = compare_board_states(
        before,
        after,
        from_phase=TurnPhase.ACTION_PHASE_START,
        to_phase=TurnPhase.ACTION_PHASE_END,
        sequence_start=10,
    )

    assert delta.complete is True
    assert [(zone.entity_id, zone.from_zone, zone.to_zone) for zone in delta.zones] == [
        (10, "HAND", "PLAY"),
        (40, "HAND", "UNKNOWN"),
        (50, "UNKNOWN", "HAND"),
    ]
    assert [entity.attribute for entity in delta.entities] == [
        "attack",
        "health",
        "max_health",
        "taunt",
        "divine_shield",
        "stealth",
        "frozen",
        "silenced",
    ]
    assert [entity.sequence for entity in delta.entities] == list(range(11, 19))
    assert next_sequence == 18
    assert delta.entities[0].value.delta == 2
    assert delta.entities[3].value.delta is None
    assert all(entity.source_card is None for entity in delta.entities)
    assert all(
        entity.information_source is InformationSource.GAMESTATE_RECONSTRUCTED
        for entity in delta.entities
    )

    assert len(delta.heroes) == 1
    assert delta.heroes[0].side is PlayerSide.PLAYER
    assert delta.heroes[0].health is not None
    assert delta.heroes[0].health.delta == -3
    assert delta.heroes[0].armor is not None
    assert delta.heroes[0].armor.delta == 2
    assert delta.heroes[0].attack is not None
    assert delta.heroes[0].attack.delta == 1
    assert len(delta.mana) == 1
    assert delta.mana[0].available is not None
    assert delta.mana[0].available.delta == -3
    assert delta.mana[0].used is not None
    assert delta.mana[0].used.delta == 3


def test_compare_board_states_marks_a_missing_boundary_as_incomplete() -> None:
    delta, next_sequence = compare_board_states(
        None,
        _board(),
        from_phase=TurnPhase.ACTION_PHASE_END,
        to_phase=TurnPhase.TURN_END,
        sequence_start=7,
    )

    assert delta.complete is False
    assert delta.entities == []
    assert delta.heroes == []
    assert delta.mana == []
    assert delta.zones == []
    assert next_sequence == 7


def test_compare_board_states_exposes_dormant_as_a_gameplay_delta() -> None:
    card = _card(20, "DORMANT")
    before = _board(board=[_minion(card, attack=4, health=5, max_health=5)])
    after = _board(board=[_minion(card, attack=4, health=5, max_health=5, dormant=True)])

    delta, _ = compare_board_states(
        before,
        after,
        from_phase=TurnPhase.ACTION_PHASE_START,
        to_phase=TurnPhase.ACTION_PHASE_END,
    )

    assert len(delta.entities) == 1
    assert delta.entities[0].attribute == "dormant"
    assert delta.entities[0].value.before is False
    assert delta.entities[0].value.after is True
    assert delta.entities[0].technical is False


def test_build_turn_state_deltas_keeps_phases_and_sequences_contiguous() -> None:
    turn = TurnState(
        turn_number=1,
        round_number=1,
        active_player=PlayerSide.PLAYER,
        turn_start_state=_attack_snapshot(1),
        action_phase_start_state=_attack_snapshot(2),
        action_phase_end_state=_attack_snapshot(4),
        turn_end_state=_attack_snapshot(3),
    )

    deltas = build_turn_state_deltas(turn)

    assert [(delta.from_phase, delta.to_phase) for delta in deltas] == [
        (TurnPhase.TURN_START, TurnPhase.ACTION_PHASE_START),
        (TurnPhase.ACTION_PHASE_START, TurnPhase.ACTION_PHASE_END),
        (TurnPhase.ACTION_PHASE_END, TurnPhase.TURN_END),
    ]
    assert all(delta.complete for delta in deltas)
    assert [delta.entities[0].sequence for delta in deltas] == [1, 2, 3]
    assert [delta.entities[0].value.delta for delta in deltas] == [1, 2, -1]


def _card(entity_id: int, card_id: str) -> CardRef:
    return CardRef(entity_id=entity_id, card_id=card_id, name=f"Carte {card_id}")


def _minion(
    card: CardRef,
    *,
    attack: int,
    health: int,
    max_health: int,
    taunt: bool = False,
    divine_shield: bool = False,
    stealth: bool = False,
    frozen: bool = False,
    silenced: bool = False,
    dormant: bool = False,
) -> MinionState:
    return MinionState(
        card=card,
        attack=attack,
        health=health,
        max_health=max_health,
        taunt=taunt,
        divine_shield=divine_shield,
        stealth=stealth,
        frozen=frozen,
        silenced=silenced,
        dormant=dormant,
    )


def _board(
    *,
    hand: list[CardRef] | None = None,
    board: list[MinionState] | None = None,
    hero: HeroState | None = None,
    mana_available: int | None = None,
    mana_used: int | None = None,
) -> BoardState:
    return BoardState(
        player=SideState(
            side=PlayerSide.PLAYER,
            hand=list(hand or []),
            board=list(board or []),
            hero=hero or HeroState(),
            mana_available=mana_available,
            mana_used=mana_used,
        ),
        opponent=SideState(side=PlayerSide.OPPONENT),
    )


def _attack_snapshot(attack: int) -> BoardState:
    return _board(
        board=[
            _minion(
                _card(20, "SEQUENCED"),
                attack=attack,
                health=4,
                max_health=4,
            )
        ]
    )
