from sydpy.component import Component#, compinit#, sydsys
import string
import os
import itertools
from subprocess import Popen, PIPE
from ddi.ddi import ddic, Dependency

wrapper_tmpl = string.Template("""
module wrap();

    import "DPI-C" pure function int xsimintf_init ();
    import "DPI-C" pure function string xsimintf_export (input string s);
    import "DPI-C" pure function string xsimintf_import ();
    import "DPI-C" pure function int xsimintf_delay ();
    
    ${port_definition}
    
    initial begin
        int      vals_read;
        automatic string       strimp;
        
        if (xsimintf_init()) begin
          $$finish;
        end
        // $$dumpfile("xsim.vdc");
        // $$dumpvars(${in_port_list}, ${out_port_list});
        // strimp = xsimintf_import();
        // $$display("INIT: %s", strimp);
        // vals_read = $$sscanf(strimp, ${import_str_format}, ${in_port_list});
    end
    
    always #0 begin
        automatic int  vals_read;
        automatic int  delay;
        automatic string       strimp;
        
        delay = xsimintf_delay();
        $$display("Delay set to %d", delay);
        if (delay > 0) begin
            #delay;
        end else if (delay < 0) begin
            $$dumpflush;
            $$finish;
        end
        strimp = xsimintf_import();
        $$display("TIMESTEP: %s", strimp);        
        vals_read = $$sscanf(strimp, ${import_str_format}, ${in_port_list});
    end

    always @(${out_port_list}) begin
        automatic string       strexp;
        automatic string       strimp;
        automatic integer      vals_read;
        
        $$sformat(strexp, ${export_str_format}, ${out_port_list});
        strimp = xsimintf_export(strexp);
        $$display("DELTA EXPORT: %s", strexp);
        // $$display("DELTA IMPORT: %s", strimp);
        // vals_read = $$sscanf(strimp, ${import_str_format}, ${in_port_list});
    end
    
    ${module_instantiation}

endmodule   
""")

module_inst_tmpl = string.Template("""
  ${module_name} ${instance_name} (
    ${port_map}
  );
""")

port_map_tmpl = string.Template(".${port_name} (${signal_name})")

def shell(cmd):
    print(' '.join(cmd))
    p = Popen(' '.join(cmd), shell=True, stdout=PIPE, stderr=PIPE, universal_newlines=True)
    out, err = p.communicate()
    print(out.rstrip(), err.rstrip())
    print("Return code: ", p.returncode)
    return out, err, p.returncode

class XsimIntf:

    state_type = {0: "S_STARTED", 1: "S_CONNECTED", 2: "S_INITIALIZED", 3:"S_IMPORT", 4:"S_EXPORT", 5:"S_DELAY"};
    cmds = {'GET_STATE': {'type': 'GET', 'params': ['state']},
            'CONTINUE': {'type': 'CONTINUE', 'params': ['state']}
            }

    #@compinit
    def __init__(self, server: Dependency('xsimserver'), log_communication=True, builddir='./xsimintf'):
        self.builddir = builddir
        self.server = server
        self.cosim_pool = []
        self.log_communication = log_communication
        ddic['sim'].events['run_start'].append(self.sim_run_start)
        ddic['sim'].events['run_end'].append(self.sim_run_end)
#         ddic['sim'].events['delta_end'].append(self.sim_delta_settled)
        ddic['sim'].events['delta_start'].append(self.sim_delta_started)
        ddic['sim'].events['delta_end'].append(self.sim_delta_ended)
        ddic['sim'].events['timestep_start'].append(self.sim_timestep_start)
        
    
    def render_module_inst(self, cosim):
        port_map = []
        for name, intf in itertools.chain(cosim.inputs.items(), cosim.outputs.items()):
            port_map.append(port_map_tmpl.substitute(port_name = name, signal_name = '_'.join([cosim.module_name, name]))) 
    
        return module_inst_tmpl.substitute(module_name=cosim.module_name,
                                           instance_name='i_' + cosim.module_name,
                                           port_map=',\n    '.join(port_map))
    
    def render_wrapper(self):
        module_insts = []
        for cosim in self.cosim_pool:
            module_insts.append(self.render_module_inst(cosim))
        
        ports_definition = []
        for name, intf in sorted(itertools.chain(self.inputs.items(), self.outputs.items())):
            if intf._get_dtype().w == 1:
                ports_definition.append('logic {0};'.format(name))
            else:
                ports_definition.append('logic [{0}:0] {1};'.format(intf._get_dtype().w-1,name))
        
        import_str_format  = ['%x']*len(self.inputs)
        export_str_format  = ['%x']*len(self.outputs)

        return wrapper_tmpl.substitute(
                                       port_definition='\n  '.join(ports_definition),
                                       import_str_format='"{0}"'.format(','.join(import_str_format)),
                                       in_port_list = ','.join(sorted(self.inputs.keys())),
                                       export_str_format='"{0}"'.format(','.join(export_str_format)),
                                       out_port_list = ','.join(sorted(self.outputs.keys())),
                                       module_instantiation = '\n\n'.join(module_insts)
                                       )

    def resolve_cosims(self):
        self.inputs = {}
        self.outputs = {}
        self.fileset = []
        for cosim in self.cosim_pool:
            cosim.resolve()
            self.inputs.update({'_'.join([cosim.module_name, k]):v for k,v in cosim.inputs.items()})
            self.outputs.update({'_'.join([cosim.module_name, k]):v for k,v in cosim.outputs.items()})
            self.fileset.extend(cosim.fileset)
    
    def send_command(self, msg_type, params = []):
        msg = "$" + msg_type
        if params:
            msg += ',' + ','.join(params)
            
        self.server.send(msg)
        if self.log_communication:
            print(msg)

        if msg_type != "CLOSE":
            ret = self.server.recv().split(',')
            if self.log_communication:
                print(ret)
            if len(ret) > 1:
                params = ret[1:]
            else:
                params = []
                
            return ret[0][1:], params
    
    def get_xsim_state(self):
        cmd_type, params = self.send_command('GET', ['state'])
        
        if cmd_type != 'RESP':
            raise Exception('Error in the connection with Xsim!')
        
        return self.state_type[int(params[0])]
    
    def recv_export(self):
        ret_type, params = self.send_command('EXPORT')
        print(ddic['sim'].time, ' : ', ddic['sim'].delta_count, ' : ', params)
        for intf, p in zip(sorted(self.outputs.items()), params):
            intf[1].write(intf[1]._get_dtype()('0x' + p.replace('x', 'u').replace('z', 'u')))
            
        ddic['sim']._update()

                  
    def send_import(self):
        cmd_type, params = self.send_command('IMPORT', [str(intf.read())[2:].replace('U', 'x') for _, intf in sorted(self.inputs.items())])

        if cmd_type != 'RESP':
            raise Exception('Error in the connection with Xsim!')
    
    def sim_run_start(self, sim):
        self.cosim_time = 0
        self.resolve_cosims()
        os.makedirs(self.builddir, exist_ok=True)
        os.chdir(self.builddir)
        
#         print(self.render_wrapper())
        text = self.render_wrapper()
#         with open(os.path.join(self.builddir, "wrapper.sv"), "w") as text_file:
        with open("wrapper.sv", "w") as text_file:
            text_file.write(text)

#         self.fileset.append(os.path.join(self.builddir, "wrapper.sv"))
        self.fileset.append('wrapper.sv')

#         files = {'sv' : [],
#                  'v'  : [],
#                  'vhd': []
#                  }
#          
#         for f in self.fileset:
#             files[os.path.splitext(f)[1][1:]].append(f)
#  
#         if files['sv']:
#             _, _, ret = shell(cmd = ['xvlog', '-sv'] + files['sv'])
#             if ret:
#                 self.server.sock.close()
#                 raise Exception('Error in HDL source compilation!') 
#           
#         if files['v']:
#             shell(cmd = ['xvlog'] + files['v'])
#           
#         if files['vhd']:
#             _,_,ret =shell(cmd = ['xvhdl'] + files['vhd'])
#             if ret:
#                 self.server.sock.close()
#                 raise Exception('Error in HDL source compilation!') 
#   
#           
#         _,_,ret = shell(cmd = ['xelab', '-m64', '-svlog', 'wrapper.sv', '-sv_root', '/home/bvukobratovic/projects/sydpy/intf/xsim/build', '-sv_lib', 'dpi', '-debug', 'all'])
#         if ret:
#             self.server.sock.close()
#             raise Exception('Error in HDL source compilation!')
#          
# #         shell(cmd = ['xsim', 'work.wrap', '-t', '/home/bvukobratovic/projects/sydpy/tests/dpi/run.tcl'])
#         cmd = ['xsim', 'work.wrap', '--runall'] #-t', '/home/bvukobratovic/projects/sydpy/tests/dpi/run.tcl']
#         self.xsim_proc = Popen(' '.join(cmd), shell=True, stdout=PIPE, stderr=PIPE, universal_newlines=True)
#         
#         xsim_state = self.get_xsim_state()
#         
#         if xsim_state != 'S_CONNECTED':
#             raise Exception('Error in the connection with Xsim!')
#         
# #         self.send_import()
        
        self.send_command('CONTINUE')
    
    def sim_run_end(self, sim):
        xsim_state = None
        while (xsim_state != 'S_DELAY'):
            xsim_state = self.get_xsim_state()
            if xsim_state != 'S_DELAY':
                self.send_command('CONTINUE')
            
        self.send_command('CLOSE')
#         self.xsim_proc.terminate()
    
    def sim_timestep_start(self, time, sim):
        if time > 0:
#             xsim_state = self.get_xsim_state()
#             while xsim_state == 'S_EXPORT':
#                 # The XSIM either:
#                 #    - Waits at the end of the delta cycle (the always block) for new inputs
#                 #    - Waits to receive the timestep waiting period (delay)
#                 print('From Timestep Start : ')
#                 self.recv_export()
#                 # If XSIM waits at the end of the delta cycle, just send it to CONTINUE, this will affect no input signals, hence XSIM will move straight to timestep process
#                 self.send_command('CONTINUE')
#                 xsim_state = self.get_xsim_state()
            print('SIM TIMESTEP: ', self.get_xsim_state())
            self.send_command('SET', ['delay', str(time - self.cosim_time)])
            self.send_command('CONTINUE')

        self.cosim_time = time
        
        return True
    
    def sim_delta_started(self, sim, time, delta):
#         
#         print('SIM DELTA STARTED: ', self.get_xsim_state())
#         self.send_command('SET', ['delay', '0'])
#         self.send_command('CONTINUE')
#         self.send_import()
#         self.send_command('CONTINUE')
        return True
    
    def sim_delta_ended(self, sim, time, delta):
        print('SIM DELTA ENDED: ', self.get_xsim_state())
        if self.get_xsim_state() == 'S_DELAY':
            self.send_command('SET', ['delay', '0'])
            self.send_command('CONTINUE')
        
        self.send_import()
        self.send_command('CONTINUE')
        
        if self.get_xsim_state() == 'S_EXPORT':
            self.recv_export()
            self.send_command('CONTINUE')
            
        while (not (ddic['sim']._ready_pool or ddic['sim'].trig_pool)):
            print('SIM DELTA SETTLED: ', self.get_xsim_state())
            self.send_command('SET', ['delay', '0'])
            self.send_command('CONTINUE')
            self.send_import()
            self.send_command('CONTINUE')
            
            if self.get_xsim_state() == 'S_EXPORT':
                self.recv_export()
                self.send_command('CONTINUE')
            else:
                break

            
        return True
    
#     def sim_delta_settled(self, sim, time, delta):
#         if xsim_state == 'S_EXPORT'
#         # The XSIM is either in: 
#         #    - initial import - This is the first finished sydpy delta cycle in the simultaion, 
#         #    - timestep import - This is the first finished sydpy delta cycle in this timestep 
#         #    - delta cycle import state
#         self.send_import()
#         
#         self.send_command('CONTINUE')
# #         xsim_state = self.get_xsim_state()
#         self.recv_export()
# 
#         # The XSIM either:
#         #    - Entered new delta cycle (the always block) since its outputs have changed
#         #    - Entered the timestep process and is waiting for the length of the waiting period, since its outputs have settled
# #         while (xsim_state == 'S_EXPORT') and (not (ddic['sim']._ready_pool or ddic['sim'].trig_pool)):
# #             self.recv_export()
# #             if xsim_state == 'S_EXPORT':
# #                 self.send_command('CONTINUE')
# #             xsim_state = self.get_xsim_state()
#             
#         #if xsim_state != 'S_DELAY':
# #             from time import sleep
# #             while (1):
# #                 #xsim_state = self.get_xsim_state()
# #                 self.send_command('CONTINUE')
# #                 print(xsim_state)
# #                 sleep(0.5)
#         #    raise Exception('Error in the connection with Xsim!')
#         
#         return True
#                     
    def updated(self, cosim):
#         self.send_import()
#         
#         self.send_command('CONTINUE')
#         xsim_state = self.get_xsim_state()
# 
#         # The XSIM either:
#         #    - Entered new delta cycle (the always block) since its outputs have changed
#         #    - Entered the timestep process and is waiting for the length of the waiting period, since its outputs have settled
#         while (xsim_state == 'S_EXPORT') and (not (ddic['sim']._ready_pool or ddic['sim'].trig_pool)):
#             self.recv_export()
#             if xsim_state == 'S_EXPORT':
#                 self.send_command('CONTINUE')
#             xsim_state = self.get_xsim_state()
#         self.update = True
        pass
    
    def register(self, cosim):
        self.cosim_pool.append(cosim)
        
    def __del__(self):
        try:
            self.server.send('$CLOSE')
#             self.xsim_proc.terminate()
        except:
            pass

def generate_wrapper(modules):
    for name, port_list in modules:
        pass

def extract_port_struct(port_list): 
    ports = {}
    for port in port_list:
        cur_level = ports
        path = port.split('.')
        
        for i, p in enumerate(path):
            if '[' in p:
                name, index = p.split('[')
                index = index.split(']')[0]
            else:
                name = p
                index = None
                
            if name not in cur_level:
                if i < len(path) - 1:
                    cur_level[name] = {}
                else:
                    cur_level[name] = 0
            
            if i < len(path) - 1:
                if index is not None:
                    cur_level = cur_level[name][index]    
                else:
                    cur_level = cur_level[name]
            else:
                cur_level[name] += 1
    
    return ports

ports_str = r'clk int2\\.ready int2\\.request int2\\.value[0] int2\\.value[1] int2\\.value[2] int2\\.value[3] rst'        
extract_port_struct(ports_str.replace(r'\\', '').split(' '))