from hscoach.models import Card, CardRef, Visibility


def test_card_model_keeps_french_content() -> None:
    card = Card(id="TEST_001", name="Carte française", text="Cri de guerre : test.")
    ref = CardRef(entity_id=42, card_id=card.id, name=card.name, text=card.text)

    assert ref.name == "Carte française"
    assert ref.visibility is Visibility.KNOWN
