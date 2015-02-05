import construct
import select
import socket
import array
import pygame
from H264Decoder import H264Decoder

pygame.init()
#pygame.display.set_mode([854, 480], pygame.RESIZABLE)
pygame.display.set_mode([854, 480])
pygame.display.set_caption("drc-sim")
done = False
pygame.joystick.init()
joystick = pygame.joystick.Joystick(0)
joystick.init()

def service_addend(ip):
    if int(ip.split('.')[3]) == 10:
        return 0
    else:
        return 100

def udp_service(ip, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((ip, port + service_addend(ip)))
    return sock

PORT_MSG = 50010
PORT_VID = 50020
PORT_AUD = 50021
PORT_HID = 50022
PORT_CMD = 50023

# hack for now, replace with dhcp result
LOCAL_IP = '192.168.1.11'

MSG_S = udp_service(LOCAL_IP, PORT_MSG)
VID_S = udp_service(LOCAL_IP, PORT_VID)

class ServiceBase(object):
    def __init__(s):
        s.seq_id_expect = None

    def update_seq_id(s, seq_id):
        ret = True
        if s.seq_id_expect == None: s.seq_id_expect = seq_id
        elif s.seq_id_expect != seq_id:
            ret = False
        s.seq_id_expect = (seq_id + 1) & 0x3ff
        return ret

    def close(s):
        pass

class ServiceVSTRM(ServiceBase):
    dimensions = {
        'camera' : (640, 480),
        'gamepad' : (854, 480)
    }

    def __init__(s):
        super(ServiceVSTRM, s).__init__()
        s.decoder = H264Decoder(
            s.dimensions['gamepad'],
            pygame.display.get_surface().get_size())
        s.header = construct.BitStruct('VSTRMHeader',
            construct.Nibble('magic'),
            construct.BitField('packet_type', 2),
            construct.BitField('seq_id', 10),
            construct.Flag('init'),
            construct.Flag('frame_begin'),
            construct.Flag('chunk_end'),
            construct.Flag('frame_end'),
            construct.Flag('has_timestamp'),
            construct.BitField('payload_size', 11),
            construct.BitField('timestamp', 32)
        )
        s.frame = array.array('B')
        s.is_streaming = False
        s.frame_decode_num = 0

    def close(s):
        s.decoder.close()

    def packet_is_idr(s, packet):
        return packet[8:16].find('\x80') != -1

    def h264_nal_encapsulate(s, is_idr, vstrm):
        slice_header = 0x25b804ff if is_idr else (0x21e003ff | ((s.frame_decode_num & 0xff) << 13))
        s.frame_decode_num += 1

        nals = array.array('B')
        # TODO shouldn't really need this after the first IDR
        # TODO hardcoded for gamepad for now
        # allow decoder to know stream parameters
        if is_idr:
            nals.extend([
                # sps
                0x00, 0x00, 0x00, 0x01,
                0x67, 0x64, 0x00, 0x20, 0xac, 0x2b, 0x40, 0x6c, 0x1e, 0xf3, 0x68,
                # pps
                0x00, 0x00, 0x00, 0x01,
                0x68, 0xee, 0x06, 0x0c, 0xe8
            ])

        # begin slice nalu
        nals.extend([0x00, 0x00, 0x00, 0x01])
        nals.extend([(slice_header >> 24) & 0xff,
                     (slice_header >> 16) & 0xff,
                     (slice_header >>  8) & 0xff,
                      slice_header & 0xff])

        # add escape codes
        nals.extend(vstrm[:2])
        for i in xrange(2, len(vstrm)):
            if vstrm[i] <= 3 and nals[-2] == 0 and nals[-1] == 0:
                nals.extend([3])
            nals.extend([vstrm[i]])

        return nals

    def update(s, packet):
        h = s.header.parse(packet)
        is_idr = s.packet_is_idr(packet)

        seq_ok = s.update_seq_id(h.seq_id)

        if not seq_ok:
            s.is_streaming = False

        if h.frame_begin:
            s.frame = array.array('B')
            if s.is_streaming == False:
                if is_idr:
                    s.is_streaming = True
                else:
                    # request a new IDR frame
                    MSG_S.sendto('\1\0\0\0', ('192.168.1.10', PORT_MSG))
                    return

        s.frame.fromstring(packet[16:])

        if s.is_streaming and h.frame_end:
            nals = s.h264_nal_encapsulate(is_idr, s.frame)
            s.decoder.display_frame(nals.tostring())

    def resize_output(s, (x, y)):
        d = s.dimensions['gamepad']
        fit = pygame.Rect((0, 0), d).fit(pygame.display.get_surface().get_rect())
        s.decoder.update_dimensions(d, fit.size)

class ServiceMSG(ServiceBase):
    def update(s, packet):
        print 'MSG', packet.encode('hex')

class ServiceNOP(ServiceBase):
    def update(s, packet):
        pass

service_handlers = {
    MSG_S : ServiceMSG(),
    VID_S : ServiceVSTRM(),
}


while not done:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            done = True
        elif event.type == pygame.VIDEORESIZE:
            pygame.display.set_mode(event.size, pygame.RESIZABLE)
            service_handlers[VID_S].resize_output(event.size)
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_BACKSLASH:
                MSG_S.sendto('\1\0\0\0', ('192.168.1.10', PORT_MSG))

    rlist, wlist, xlist = select.select(service_handlers.keys(), (), (), 1)

    if not rlist:
        continue

    for sock in rlist:
        service_handlers[sock].update(sock.recvfrom(2048)[0])

for s in service_handlers.itervalues():
    s.close()

pygame.quit()
