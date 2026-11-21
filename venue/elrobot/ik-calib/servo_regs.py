"""Read-only dump of the follower servo registers (voltage, torque limits, gains, load, status) through Station.
Run from norma-core/software/station/examples/elrobot-ik-api:  uv run python <path>/servo_regs.py [BUS_SERIAL]
"""
import asyncio, sys
from elrobot_ik.station.client import StationClient
from elrobot_ik.station.proto import st3215_pb2
u16=lambda d,a: int.from_bytes(d[a:a+2],'little') if len(d)>=a+2 else -1
u8=lambda d,a: d[a] if len(d)>a else -1
async def main():
    c=StationClient("ws://localhost:8889/api"); await c.start()
    assert await c.wait_frame(10), "no frame"
    st=st3215_pb2.InferenceState.FromString(c.latest_frame)
    for b in st.buses:
        if b.bus.serial_number!=(sys.argv[1] if len(sys.argv)>1 else "5B79016927"): continue
        print("bus",b.bus.serial_number)
        print(" id  volt  maxTorqEE  P  D  I  protCur  torqLimRAM  load  curr  temp  pos  torque")
        for m in sorted(b.motors,key=lambda m:m.id):
            d=m.state
            err = st3215_pb2.ST3215Error.ST3215ErrorKind.Name(m.error.kind) if m.HasField("error") else "-"
            print(f" M{m.id} st=0x{u8(d,0x41):02x} {err}  {u8(d,0x3E)/10:4.1f}  {u16(d,0x10):9}  {u8(d,0x15):2} {u8(d,0x16):2} {u8(d,0x17):2}  {u16(d,0x1C):7}  {u16(d,0x30):10}  {u16(d,0x3C):4}  {u16(d,0x45):4}  {u8(d,0x3F):4}  {u16(d,0x38):4}  {u8(d,0x28)}")
    await c.stop()
asyncio.run(main())
