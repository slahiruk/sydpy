import sydpy
from tests.packer_coef_calc import JesdPackerAlgo
from sydpy.types.bit import Bit

def SymbolicBit(w):
    return type('symbit', (SymbolicBitABC,), dict(w=w))

class SymbolicBitABC:
    w = 1
    
    def __init__(self, val=[], vld=None, defval = 0):
        try:
            l = len(val)
        except:
            val = []
            l = 0
            
        self.val = val + [defval]*(self.w - l)
        
    def __mod__(self, other):
        return SymbolicBit(self.w + other.w)(val = other.val + self.val)

    def __len__(self):
        return self.w

    def __str__(self):
        return str(self.val)

    def __repr__(self):
        return repr(self.val)

    def __getitem__(self, key):
        if isinstance( key, slice ) :
            high = max(key.start, key.stop)
            low = min(key.start, key.stop)
        elif isinstance( key, int ) :
            high = low = int(key)
        else:
            raise TypeError("Invalid argument type.")
        
        return SymbolicBit(high-low+1)(val = self.val[low:(high+1)])

class PackerTlMatrix(sydpy.Component, JesdPackerAlgo):

    def __init__(self, name, ch_samples, tSample = None, arch='tlm', jesd_params=dict(M=1, N=8, S=1, CS=0, CF=0, L=1, F=1, HD=0), **kwargs):
        sydpy.Component.__init__(self, name)
        dtype = SymbolicBit
        JesdPackerAlgo.__init__(self, dtype=dtype, jesd_params=jesd_params)
        self.jesd_params = jesd_params
            
        if arch == 'tlm':        
            
            sym_samples = []
            for i in range(jesd_params['M']):
                sym_samples.append((dtype(jesd_params['N'])([(i, 0, j) for j in range(jesd_params['N'])]), 
                                dtype(jesd_params['CS'])([(i, 1, j) for j in range(jesd_params['CS'])])))    
        
            print('Samples: ', sym_samples)
            self.pack_m = JesdPackerAlgo.pack(self, sym_samples)
            
            self.csin = []
            self.din = []
            for i, d in enumerate(ch_samples):
                self.din.append(self.inst(sydpy.Itlm, 'din{}'.format(i), dtype=tSample, dflt={'d': 0, 'cs':0}))
                d >>= self.din[-1]
            
            self.inst(sydpy.Itlm, 'frame')
            self.inst(sydpy.Process, 'pack', self.pack)
        elif arch == 'seq':
            
            sym_samples = []
            for i in range(jesd_params['M']):
                sym_samples.append((dtype(jesd_params['N'])([(i, j) for j in range(jesd_params['N'])]), 
                                dtype(jesd_params['CS'])([(i, j + jesd_params['N']) for j in range(jesd_params['CS'])])))  

            print('Samples: ', sym_samples)
            self.pack_m = JesdPackerAlgo.pack(self, sym_samples)
            
            self.inst(sydpy.Iseq, 'frame')
            self.c['frame'].c['valid'] <<= False
            self.inst(sydpy.Process, 'pack_seq', self.pack_seq, senslist=[self.c['frame'].c['clk'].e['posedge']])
            self.idin = []
            for i, d in enumerate(ch_samples):
#                 idin = self.inst(sydpy.Iseq, 'din{}'.format(i), dtype=tSample, dflt={'d': 0, 'cs':0}, clk=self.c['frame'].c['clk'])
                idin = self.inst(sydpy.Iseq, 'din{}'.format(i), dtype=Bit(tSample.dtype['d'].w + tSample.dtype['cs'].w), dflt=0, clk=self.c['frame'].c['clk'])
                self.idin.append(idin)
                d >>= self.c['din{}'.format(i)]
    
    def pack_seq(self):
        
        frame = []
        for m_lane in self.pack_m:
            f_lane = []
            for m_byte in m_lane:
                f_byte = sydpy.Bit(8)(0)
                for i, m_bit in enumerate(m_byte.val):
                    if m_bit:
                        f_byte[i] = self.idin[m_bit[0]].c['data'][m_bit[1]]
                    else:
                        f_byte[i] = 0
                f_lane.append(f_byte)
                
            frame.append(f_lane)
        
        self.c['frame'].c['valid'] <<= True
        self.c['frame'] <<= frame
        
        print()
        print('Matrix Output Frame:')
        print()
        for l in frame:
            print(l)
    
    def pack(self):
        while(1):
            sydpy.ddic['sim'].wait(sydpy.Delay(10))
            samples = []
            for _ in range(self.jesd_params['S']):
                for d in self.din:
                    samples.append(d.bpop())
            
            frame = []
            for m_lane in self.pack_m:
                f_lane = []
                for m_byte in m_lane:
                    f_byte = sydpy.Bit(8)(0)
                    for i, m_bit in enumerate(m_byte.val):
                        if m_bit:
                            f_byte[i] = samples[m_bit[0]][m_bit[1]][m_bit[2]]
                        else:
                            f_byte[i] = 0
                    f_lane.append(f_byte)
                    
                frame.append(f_lane)
                
            self.c['frame'].push(frame)
                
            print()
            print('Matrix Output Frame:')
            print()
            for l in frame:
                print(l)
