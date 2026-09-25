"""Verify referee signatures against a real record from d-close1-price."""

from flopstar.config import REFEREE_DID
from flopstar.didkey import verify_signature

# d-close1-price seq 33, as served by /r/d-close1-price?format=json
ROOM = "d-close1-price"
NONCE = 1790347704958
TEXT = (
    '{"age_s":3002,"applied":"225.03",'
    '"file":"31f427a0ec705b5a753a2a55512f014b97829842bb1104e605dd583292c563d4",'
    '"for":33,"global":"224.15","limits":["213.78","236.28"],"n":32,'
    '"ref":{"px":"225.03","tid":745739478454287,"time":"2026-09-25T13:49:57.723000Z"},'
    '"t":"price"}'
)
SIG = "m0qvBEJfs1OYSA9_P9AUE6w2tXFfE2DdNruUyyMsFfWtjwCLGn1mE39P3DWG90uyyJ2_KFzxLIvQmYAJFdh1Dg"


def test_real_referee_message_verifies():
    assert verify_signature(REFEREE_DID, ROOM, str(NONCE), TEXT, SIG)


def test_tampered_text_fails():
    assert not verify_signature(REFEREE_DID, ROOM, str(NONCE), TEXT.replace("225.03", "225.04"), SIG)


def test_wrong_room_or_nonce_fails():
    assert not verify_signature(REFEREE_DID, "d-close1-flow", str(NONCE), TEXT, SIG)
    assert not verify_signature(REFEREE_DID, ROOM, str(NONCE + 1), TEXT, SIG)


def test_malformed_signature_fails():
    assert not verify_signature(REFEREE_DID, ROOM, str(NONCE), TEXT, SIG[:-1])
    assert not verify_signature(REFEREE_DID, ROOM, str(NONCE), TEXT, "zz" * 43)
