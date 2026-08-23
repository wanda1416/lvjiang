"""Win32 RAWINPUT 结构解析测试（不需要 Windows 消息循环）。"""

from lvjiang.core.desktop import raw_input


def _packet(dx: int, dy: int, *, absolute: bool = False) -> bytes:
    packet = raw_input._RawInput()
    packet.header.dwType = raw_input._RIM_TYPEMOUSE
    packet.header.dwSize = raw_input.ctypes.sizeof(packet)
    packet.mouse.usFlags = raw_input._MOUSE_MOVE_ABSOLUTE if absolute else 0
    packet.mouse.lLastX = dx
    packet.mouse.lLastY = dy
    return bytes(packet)


def test_decode_relative_raw_mouse_packet():
    assert raw_input.decode_raw_mouse(_packet(-17, 9)) == (-17, 9)


def test_raw_input_structures_match_win32_abi_sizes():
    pointer_size = raw_input.ctypes.sizeof(raw_input.ctypes.c_void_p)
    assert raw_input.ctypes.sizeof(raw_input._RawMouse) == 24
    assert raw_input.ctypes.sizeof(raw_input._RawInputHeader) == (
        24 if pointer_size == 8 else 16)
    assert raw_input.ctypes.sizeof(raw_input._RawInput) == (
        48 if pointer_size == 8 else 40)


def test_decode_ignores_absolute_raw_mouse_packet():
    assert raw_input.decode_raw_mouse(_packet(100, 200, absolute=True)) is None


def test_decode_rejects_truncated_packet():
    assert raw_input.decode_raw_mouse(b"\0" * 4) is None


def test_message_time_preserves_queue_delay_in_monotonic_clock():
    timestamp = raw_input.message_time_to_monotonic_ns(
        message_time_ms=990,
        current_tick_ms=1000,
        current_monotonic_ns=5_000_000_000,
    )
    assert timestamp == 4_990_000_000


def test_message_time_expands_32_bit_tick_wrap():
    current = (1 << 32) + 0x10
    timestamp = raw_input.message_time_to_monotonic_ns(
        message_time_ms=0xFFFFFFF0,
        current_tick_ms=current,
        current_monotonic_ns=10_000_000_000,
    )
    assert timestamp == 9_968_000_000
