import socket
import threading
import time
import sys
import mido
import os
import configparser
from zeroconf import Zeroconf, ServiceInfo

class MIDISensorController:
    def __init__(self, port=8081):
        self.port = port
        self.sock = None
        self.running = False
        self.cc1_value = 0.0  # 维护的cc1数据变量
        self.cc11_value = 0.0  # 接收到的cc11数据
        self.cc_opt_value = 0.0  # 接收到的cc_opt数据
        self.last_cc1 = 0.0  # 上一个cc1值
        self.temp_cc1 = 0.0  # 临时cc1变量
        self.peak_flag = False  # 标记变量
        self.last_st = 0.0  # 上一个预测值
        self.new_st = 0.0  # 当前预测值
        self.last_cc11 = 0.0  # 上一个cc11值
        self.last_st_cc11 = 0.0  # 上一个cc11预测值
        self.new_st_cc11 = 0.0  # 当前cc11预测值
        self.aef = 0.1  # 全局平滑指数（向后兼容）
        self.aef_cc1 = 0.1  # CC1平滑指数
        self.aef_cc11 = 0.1  # CC11平滑指数
        self.cc1_max = 30  # cc1最大值
        self.send_frequency = 60  # 发送频率，默认60Hz

        # MIDI CC控制器开关状态，默认都为开启
        self.cc1_enabled = True
        self.cc11_enabled = True
        self.cc_opt_enabled = False

        #MIDI CC 映射
        self.cc1_mapping = 1
        self.cc11_mapping = 11
        self.cc_opt_mapping = 3

        # 读取传感器处理方式
        self.cc1_smooth = "smooth"
        self.cc11_smooth = None
        self.cc_opt_smooth = None

        # 参数监控显示模式，默认为text
        self.para_monitor_display = "text"

        self.midi_output = None
        self.send_thread = None
        self.listen_thread = None
        self.last_data_time = time.time()  # 记录上次接收数据的时间
        self.data_timeout = 1.0  # 超时阈值为1秒
        self.is_data_timeout = False  # 是否超时状态
        self.zeroconf = None
        self.service_info = None
        self.load_settings()  # 加载配置文件

    def get_resource_path(self, relative_path):
        """获取资源文件的绝对路径，支持开发环境和打包环境"""
        # 检查是否为Nuitka打包环境
        if "__compiled__" in globals():
            # Nuitka打包环境，配置文件应与exe文件在同一目录
            base_path = os.path.dirname(os.path.abspath(sys.executable))
        elif getattr(sys, 'frozen', False):
            # PyInstaller打包环境
            base_path = os.path.dirname(sys.executable)
        else:
            # 开发环境
            base_path = os.path.dirname(os.path.abspath(__file__))

        return os.path.join(base_path, relative_path)

    def get_local_ip(self):
        """获取本机在局域网中的IP地址"""
        try:
            # 创建一个UDP socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # 连接到一个远程地址（不会实际发送数据）
            s.connect(("8.8.8.8", 80))
            # 获取本地IP地址
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            # 如果无法获取，返回默认值
            return "127.0.0.1"

    def register_mdns_service(self):
        """注册mDNS服务"""
        try:
            self.zeroconf = Zeroconf()
            local_ip = self.get_local_ip()

            # 创建服务信息
            self.service_info = ServiceInfo(
                "_midi._tcp.local.",
                "MIDISensorController._midi._tcp.local.",
                addresses=[socket.inet_aton(local_ip)],
                port=self.port,
                properties={'description': 'MIDI Sensor Controller'},
                server="MIDISensorController.local.",
            )

            # 注册服务
            self.zeroconf.register_service(self.service_info)
            print(f"已注册mDNS服务: MIDISensorController._midi._tcp.local.")
            #print(f"本机IP地址: {local_ip}")
            return True
        except Exception as e:
            print(f"注册mDNS服务失败: {e}")
            return False

    def unregister_mdns_service(self):
        """注销mDNS服务"""
        if self.zeroconf and self.service_info:
            try:
                # 注销服务
                self.zeroconf.unregister_service(self.service_info)
                # 关闭zeroconf，这会等待所有操作完成
                self.zeroconf.close()
                print("已注销mDNS服务")
            except Exception as e:
                print(f"注销mDNS服务失败: {e}")
            finally:
                self.zeroconf = None
                self.service_info = None

    def load_settings(self):
        """从同目录下的set.ini文件加载设置"""
        try:
            # 获取配置文件路径
            settings_file = self.get_resource_path("set.ini")

            # 打印调试信息
            print(f"正在查找配置文件: {settings_file}")

            # 检查文件是否存在
            if not os.path.exists(settings_file):
                print("配置文件set.ini不存在，使用默认设置")
                return

            print(f"找到配置文件: {settings_file}")

            # 使用configparser读取INI格式配置文件
            config = configparser.ConfigParser()
            config.read(settings_file, encoding='utf-8')

            # 读取配置项
            if config.has_section('MIDIController'):
                # 读取全局aef（向后兼容）
                if config.has_option('MIDIController', 'aef'):
                    self.aef = config.getfloat('MIDIController', 'aef')
                    print(f"已加载 aef = {self.aef}")

                # 读取CC1专用aef
                if config.has_option('MIDIController', 'aef_cc1'):
                    self.aef_cc1 = config.getfloat('MIDIController', 'aef_cc1')
                    print(f"已加载 aef_cc1 = {self.aef_cc1}")
                else:
                    # 如果没有单独设置，使用全局aef值
                    self.aef_cc1 = self.aef
                    print(f"使用全局aef值作为aef_cc1: {self.aef_cc1}")

                # 读取CC11专用aef
                if config.has_option('MIDIController', 'aef_cc11'):
                    self.aef_cc11 = config.getfloat('MIDIController', 'aef_cc11')
                    print(f"已加载 aef_cc11 = {self.aef_cc11}")
                else:
                    # 如果没有单独设置，使用全局aef值
                    self.aef_cc11 = self.aef
                    print(f"使用全局aef值作为aef_cc11: {self.aef_cc11}")

                if config.has_option('MIDIController', 'cc1_max'):
                    self.cc1_max = config.getfloat('MIDIController', 'cc1_max')
                    print(f"已加载 cc1_max = {self.cc1_max}")

                if config.has_option('MIDIController', 'send_frequency'):
                    self.send_frequency = config.getfloat('MIDIController', 'send_frequency')
                    print(f"已加载 send_frequency = {self.send_frequency}")

                # 读取监听端口号配置项
                if config.has_option('MIDIController', 'listen_port'):
                    self.port = config.getint('MIDIController', 'listen_port')
                    print(f"已加载 listen_port = {self.port}")

                # 读取MIDI CC控制器开关状态
                if config.has_option('MIDIController', 'cc1_enabled'):
                    self.cc1_enabled = config.getboolean('MIDIController', 'cc1_enabled')
                if config.has_option('MIDIController', 'cc11_enabled'):
                    self.cc11_enabled = config.getboolean('MIDIController', 'cc11_enabled')
                if config.has_option('MIDIController', 'cc_opt_enabled'):
                    self.cc_opt_enabled = config.getboolean('MIDIController', 'cc_opt_enabled')
            print(f"MIDI CC控制器状态: cc1={self.cc1_enabled}, cc11={self.cc11_enabled}, cc_opt={self.cc_opt_enabled}")

            if config.has_section('Sensors'):
                if config.has_option('Sensors', 'cc1'):
                    self.cc1_smooth = config.get('Sensors', 'cc1').lower() == 'smooth'
                if config.has_option('Sensors', 'cc11'):
                    self.cc11_smooth = config.get('Sensors', 'cc11').lower() == 'smooth'
                if config.has_option('Sensors', 'cc_opt'):
                    print("cc_opt由手机距离传感器控制，只有0/1两个值，不支持平滑计算-_-")
            print(f"传感器处理方式: cc1={'smooth' if self.cc1_smooth else 'none'}, cc11={'smooth' if self.cc11_smooth else 'none'}")

            # 读取参数监控显示模式
            if config.has_option('Display', 'para_monitor_display'):
                self.para_monitor_display = config.get('Display', 'para_monitor_display').lower()
            print(f"\n参数监控显示方式：{self.para_monitor_display}")

            # 读取MIDI CC映射
            if config.has_section('MIDIMapping'):
                if config.has_option('MIDIMapping', 'cc1'):
                    self.cc1_mapping = config.getint('MIDIMapping', 'cc1')

                if config.has_option('MIDIMapping', 'cc11'):
                    self.cc11_mapping = config.getint('MIDIMapping', 'cc11')

                if config.has_option('MIDIMapping', 'cc_opt'):
                    self.cc_opt_mapping = config.getint('MIDIMapping', 'cc_opt')
            
                # 检查映射是否冲突
                mappings = [('cc1', self.cc1_mapping), ('cc11', self.cc11_mapping), ('cc_opt', self.cc_opt_mapping)]
                conflict_found = False
                for i in range(len(mappings)):
                    for j in range(i+1, len(mappings)):
                        if mappings[i][1] == mappings[j][1]:
                            conflict_found = True
                            break
                    if conflict_found:
                        break
                
                if conflict_found:
                    print("错误：检测到MIDI映射冲突，请检查配置文件")
                    print("将使用默认映射")
                    self.cc1_mapping = 1
                    self.cc11_mapping = 11
                    self.cc_opt_mapping = 3
            print(f"MIDI CC控制器映射: cc1={self.cc1_mapping}, cc11={self.cc11_mapping}, cc_opt={self.cc_opt_mapping}\n")

        except Exception as e:
            print(f"读取配置文件出错: {e}，使用默认设置")

    def list_and_select_port(self):
        """
        列出所有可用的MIDI输出端口并让用户选择
        """
        # 获取所有可用的MIDI输出端口
        available_ports = mido.get_output_names()
        
        # 自动过滤掉"Microsoft GS Wavetable Synth"端口
        filtered_ports = [port for port in available_ports if "Microsoft GS Wavetable Synth" not in port]

        # 检查是否有可用端口
        if not available_ports:
            print("错误：没有找到任何可用的MIDI输出端口。")
            print("\n解决方案：")
            print("1. 确保MIDI设备已正确连接到计算机")
            print("2. 检查设备驱动程序是否已正确安装")
            print("3. 尝试重新插拔MIDI设备")
            print("4. 以管理员权限运行此程序")
            input("\n按Enter键继续...")
            return None

        # 特殊情况：只有Microsoft GS Wavetable Synth端口
        if not filtered_ports and available_ports:
            synth_ports = [port for port in available_ports if "Microsoft GS Wavetable Synth" in port]
            if synth_ports:
                print(f"警告：当前仅存在Microsoft GS Wavetable Synth的MIDI端口，已自动链接，但请注意，Microsoft GS Wavetable Synth非正常音源")
                print(f"自动选择端口: {synth_ports[0]}")
                return synth_ports[0]

        # 列出所有可用端口（不包括被过滤的）
        print("可用的MIDI输出端口：")
        for i, port in enumerate(filtered_ports):
            print(f"{i + 1}. {port}")

        # 如果只有一个端口，直接选择它
        if len(filtered_ports) == 1:
            print(f"\n只有一个可用端口，自动选择: {filtered_ports[0]}")
            return filtered_ports[0]

        # 让用户选择端口
        while True:
            try:
                choice = input(
                    f"\n请选择端口 (1-{len(filtered_ports)}) 或输入端口名称 (按Enter使用第一个端口，按Ctrl+C退出): ")

                # 如果用户直接按Enter，选择第一个端口
                if choice == "":
                    return filtered_ports[0]

                # 如果用户输入的是数字
                if choice.isdigit():
                    index = int(choice) - 1
                    if 0 <= index < len(filtered_ports):
                        return filtered_ports[index]
                    else:
                        print("无效的选择，请重新输入。")
                # 如果用户输入的是端口名称
                elif choice in filtered_ports:
                    return choice
                # 允许用户输入完整端口名称，包括被过滤的端口
                elif choice in available_ports:
                    print(f"警告：'{choice}' 端口默认被过滤，但仍可使用。")
                    return choice
                else:
                    print("无效的端口名称，请重新输入。")
            except KeyboardInterrupt:
                print("\n程序已退出。")
                return None

    def initialize_midi(self):
        """初始化MIDI系统并列出可用设备"""
        port_name = self.list_and_select_port()

        if port_name is None:
            return False

        try:
            self.midi_output = mido.open_output(port_name)
            print(f"\n使用MIDI输出设备: {port_name}")
        except Exception as e:
            print(f"无法打开MIDI端口 {port_name}: {e}")
            print("\n解决方案：")
            print("1. 确保选择的MIDI端口设备可用")
            print("2. 检查设备是否被其他程序占用")
            print("3. 以管理员权限运行此程序")
            print("4. 检查设备驱动程序是否正确安装")
            input("\n按Enter键继续...")
            return False

        return True

    def map_value(self, value, in_min, in_max, out_min, out_max):
        """将值从一个范围映射到另一个范围"""
        return (value - in_min) * (out_max - out_min) / (in_max - in_min) + out_min

    def process_cc1_data(self, new_value):
        """处理cc1数据，检测极大值点"""
        # 限制cc1范围在0-cc1_max之间
        if new_value > self.cc1_max:
            new_value = self.cc1_max

        # 指数平滑滤波，使用CC1专用的aef参数
        new_st = self.aef_cc1 * self.last_cc1 + (1 - self.aef_cc1) * self.last_st

        # 更新平滑后的值
        self.last_cc1 = new_value
        self.last_st = new_st

    def process_cc11_data(self, new_value):
        """处理cc11数据，应用指数平滑滤波"""
        # 限制cc11范围在0-90之间
        new_value = max(0, min(90, new_value))

        # 指数平滑滤波，使用CC11专用的aef参数
        new_st_cc11 = self.aef_cc11 * self.last_cc11 + (1 - self.aef_cc11) * self.last_st_cc11

        # 更新平滑后的值
        self.last_cc11 = new_value
        self.last_st_cc11 = new_st_cc11

    def send_midi_data(self):
        """以指定频率发送MIDI控制信号"""
        # 初始化上一次发送的值
        last_cc1_value = None
        last_cc11_value = None
        last_cc_opt_value = None
        
        while self.running:
            try:
                # 检查是否超时
                current_time = time.time()
                if current_time - self.last_data_time > self.data_timeout:
                    if not self.is_data_timeout:  # 刚进入超时状态
                        self.is_data_timeout = True
                        # 只在进入超时状态时打印一次提示
                        print("检测到数据超时，暂停发送MIDI控制信号")
                else:
                    if self.is_data_timeout:  # 刚退出超时状态
                        self.is_data_timeout = False
                        # 只在退出超时状态时打印一次提示
                        print("恢复数据接收，继续发送MIDI控制信号")

                # 只有在非超时状态下才发送MIDI信号
                if not self.is_data_timeout:
                    # 发送到MIDI端口的cc1、cc11和cc_opt控制器（根据开关状态）
                    if self.midi_output:
                        # 发送CC1控制器消息（如果启用）
                        if self.cc1_enabled:
                            # 将cc1映射到0-127范围
                            cc1_midi = round(self.map_value(self.last_st, 0, self.cc1_max, 0, 127))
                            # 限制在有效范围内
                            cc1_midi = max(0, min(127, cc1_midi))
                            
                            # 实现更平滑的输出：使用上一次值和当前值的平均值
                            if last_cc1_value is not None:
                                smoothed_cc1 = (last_cc1_value + cc1_midi) // 2
                            else:
                                smoothed_cc1 = cc1_midi
                            
                            # 只有在值发生变化时才发送MIDI消息
                            if last_cc1_value != smoothed_cc1:
                                cc1_msg = mido.Message('control_change', control=self.cc1_mapping, value=smoothed_cc1)
                                self.midi_output.send(cc1_msg)
                                last_cc1_value = smoothed_cc1

                        # 发送CC11控制器消息（如果启用）
                        if self.cc11_enabled:
                            # 将cc11映射到0-127范围
                            cc11_midi = round(self.map_value(self.last_st_cc11, 0, 90, 0, 127))
                            # 限制在有效范围内
                            cc11_midi = max(0, min(127, cc11_midi))
                            
                            # 实现更平滑的输出：使用上一次值和当前值的平均值
                            if last_cc11_value is not None:
                                smoothed_cc11 = (last_cc11_value + cc11_midi) // 2
                            else:
                                smoothed_cc11 = cc11_midi
                            
                            # 只有在值发生变化时才发送MIDI消息
                            if last_cc11_value != smoothed_cc11:
                                cc11_msg = mido.Message('control_change', control=self.cc11_mapping, value=smoothed_cc11)
                                self.midi_output.send(cc11_msg)
                                last_cc11_value = smoothed_cc11

                        # 发送cc_opt控制器消息（如果启用）
                        if self.cc_opt_enabled:
                            # 将cc_opt映射到0-127范围
                            cc_opt_midi = round(self.map_value(self.cc_opt_value, 0, 90, 0, 127))
                            # 限制在有效范围内
                            cc_opt_midi = max(0, min(127, cc_opt_midi))
                            cc_opt_msg = mido.Message('control_change', control=self.cc_opt_mapping, value=cc_opt_midi)
                            self.midi_output.send(cc_opt_msg)

                # 根据发送频率计算睡眠时间
                sleep_time = 1.0 / self.send_frequency
                time.sleep(sleep_time)
            except Exception as e:
                if self.running:  # 只在运行时打印错误
                    print(f"MIDI发送错误: {e}")

    def listen_for_data(self):
        """监听UDP端口数据"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('0.0.0.0', self.port))
        print(f"监听端口 {self.port}")

        while self.running:
            try:
                # 设置socket超时，以便能够响应running状态的变化
                self.sock.settimeout(1.0)
                data, addr = self.sock.recvfrom(1024)
                message = data.decode('utf-8').strip()

                # 更新最后接收数据时间
                self.last_data_time = time.time()

                # 解析数据 "cc1 cc11 cc_opt"
                parts = message.split(' ')
                if len(parts) >= 1:
                    # 根据启用状态处理和显示数据
                    display_parts = []

                    # 处理cc1数据（如果启用）
                    if self.cc1_enabled and len(parts) >= 1:
                        cc1 = float(parts[0])
                        # 根据配置决定是否进行平滑处理
                        if self.cc1_smooth:
                            # 处理cc1数据
                            self.process_cc1_data(cc1)
                        else:
                            # 不进行平滑处理，直接使用原始值
                            self.last_st = cc1
                            self.last_cc1 = cc1
                        display_parts.append(f"cc1={cc1:.1f}")

                    # 更新cc11数据（如果启用）
                    if self.cc11_enabled and len(parts) >= 2:
                        cc11 = float(parts[1])
                        # 根据配置决定是否进行平滑处理
                        if self.cc11_smooth:
                            # 处理cc11数据
                            self.process_cc11_data(cc11)
                        else:
                            # 不进行平滑处理，直接使用原始值
                            self.last_st_cc11 = cc11
                            self.last_cc11 = cc11
                        display_parts.append(f"cc11={cc11:.1f}")

                    # 处理cc_opt数据（如果启用）
                    if self.cc_opt_enabled and len(parts) >= 3:
                        cc_opt = float(parts[2])
                        self.cc_opt_value = cc_opt
                        display_parts.append(f"cc_opt={cc_opt:.1f}")

                    # 根据参数控制是否打印接收到的数据信息
                    # para_monitor_display支持三种模式: graphic(图形化显示), text(文本显示), false(不显示)
                    if self.para_monitor_display != "false" and not self.is_data_timeout and display_parts:
                        if self.para_monitor_display == "graphic":
                            # 图形化显示模式
                            cc1_val = float(parts[0]) if len(parts) >= 1 and self.cc1_enabled else 0
                            cc11_val = float(parts[1]) if len(parts) >= 2 and self.cc11_enabled else 0
                            if cc1_val > cc11_val:
                                print(" "*int(cc11_val)+"||"+" "*int(cc1_val-cc11_val)+"@")
                            else:
                                print(" "*int(cc1_val)+"@"+" "*int(cc11_val-cc1_val)+"||")
                        else:
                            # 文本显示模式（默认）
                            print("接收到数据: " + ", ".join(display_parts))
                else:
                    # 即使关闭了参数监控显示，也显示无效数据格式的错误信息
                    if self.para_monitor_display:
                        print(f"无效数据格式: {message}")

            except socket.timeout:
                # socket超时，继续检查running状态
                continue
            except Exception as e:
                if self.running:  # 只在运行时打印错误
                    print(f"接收数据错误: {e}")

    def start(self):
        """启动控制器"""
        # 注册mDNS服务
        self.register_mdns_service()

        if not self.initialize_midi():
            self.unregister_mdns_service()
            return False

        self.running = True

        # 启动监听线程
        self.listen_thread = threading.Thread(target=self.listen_for_data)
        self.listen_thread.daemon = True
        self.listen_thread.start()

        # 启动MIDI发送线程
        self.send_thread = threading.Thread(target=self.send_midi_data)
        self.send_thread.daemon = True
        self.send_thread.start()

        return True

    def stop(self):
        """停止控制器"""
        self.running = False

        # 等待线程结束，但设置超时时间
        if self.listen_thread and self.listen_thread.is_alive():
            self.listen_thread.join(timeout=2.0)

        if self.send_thread and self.send_thread.is_alive():
            self.send_thread.join(timeout=2.0)

        # 关闭socket
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None

        # 关闭MIDI输出
        if self.midi_output:
            try:
                self.midi_output.close()
            except:
                pass
            self.midi_output = None

        # 注销mDNS服务
        self.unregister_mdns_service()


def main():
    # 默认端口号
    default_port = 8081

    controller = MIDISensorController(default_port)

    # 如果通过命令行指定了端口号，则使用命令行参数覆盖配置文件设置
    if len(sys.argv) > 1:
        try:
            controller.port = int(sys.argv[1])
            print(f"使用命令行指定的端口: {controller.port}")
        except ValueError:
            print(f"命令行端口参数无效，使用配置文件或默认端口: {controller.port}")

    try:
        if controller.start():
            print(f"控制器已启动，按 Ctrl+C 停止...")
            # 保持主线程运行
            while True:
                time.sleep(1)
        else:
            print("控制器启动失败")
    except KeyboardInterrupt:
        print("\n正在停止控制器...")
    finally:
        controller.stop()
        print("控制器已停止")


if __name__ == "__main__":
    main()