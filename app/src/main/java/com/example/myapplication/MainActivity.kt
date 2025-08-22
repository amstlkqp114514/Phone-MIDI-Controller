package com.example.myapplication

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.*
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import kotlin.math.sqrt
import kotlin.math.PI
import kotlin.concurrent.thread

class MainActivity : AppCompatActivity(), SensorEventListener {
    private lateinit var sensorManager: SensorManager
    private var linearAccelerationSensor: Sensor? = null
    private var rotationVectorSensor: Sensor? = null
    private var proximitySensor: Sensor? = null
    
    // 传感器数据
    private var cc1: Float = 0.0f  // 运动方向上的加速度
    private var cc11: Float = 0.0f // 手机平面与水平面夹角
    private var cc12: Float = 0.0f // 距离传感器数据
    
    // 传感器原始数据
    private var linearAccelData = FloatArray(3)
    private var rotationVectorData = FloatArray(5)
    private var proximityData: Float = 0.0f
    private var rotationMatrix = FloatArray(9)
    private var orientationAngles = FloatArray(3)
    
    // UI组件
    private lateinit var ipEditText: EditText
    private lateinit var portEditText: EditText
    private lateinit var discoverButton: Button
    private lateinit var startButton: Button
    private lateinit var stopButton: Button
    private lateinit var rawDataTextView: TextView
    private lateinit var processedDataTextView: TextView
    private lateinit var statusTextView: TextView
    private lateinit var ipListView: ListView
    private lateinit var ipListAdapter: ArrayAdapter<String>
    private val ipList = mutableListOf<String>()
    private var isIpListVisible = false
    
    // 网络发现
    private var nsdManager: NsdManager? = null
    private var discoveryListener: NsdManager.DiscoveryListener? = null
    private var resolveListener: NsdManager.ResolveListener? = null
    
    // 网络通信
    private var isSending = false
    private var datagramSocket: DatagramSocket? = null
    private val handler = Handler(Looper.getMainLooper())
    private val sendDataRunnable = object : Runnable {
        override fun run() {
            if (isSending) {
                sendData()
                handler.postDelayed(this, 16) // 约60Hz (16.67ms)
            }
        }
    }
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_main)
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.main)) { v, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(systemBars.left, systemBars.top, systemBars.right, systemBars.bottom)
            insets
        }
        
        initViews()
        initSensors()
        setupEventListeners()
        initNsd()
    }
    
    private fun initViews() {
        ipEditText = findViewById(R.id.ipEditText)
        portEditText = findViewById(R.id.portEditText)
        discoverButton = findViewById(R.id.discoverButton)
        startButton = findViewById(R.id.startButton)
        stopButton = findViewById(R.id.stopButton)
        rawDataTextView = findViewById(R.id.rawDataTextView)
        processedDataTextView = findViewById(R.id.processedDataTextView)
        statusTextView = findViewById(R.id.statusTextView)
        ipListView = findViewById(R.id.ipListView)
        
        // 设置默认值
        ipEditText.setText("192.168.1.100")
        portEditText.setText("8080")
        
        // 初始化IP地址列表
        ipListAdapter = ArrayAdapter(this, android.R.layout.simple_list_item_1, ipList)
        ipListView.adapter = ipListAdapter
        ipListView.visibility = View.GONE
        
        // 设置列表项点击事件
        ipListView.onItemClickListener = AdapterView.OnItemClickListener { _, _, position, _ ->
            val selectedIp = ipList[position].split(":")[0] // 提取IP地址部分
            ipEditText.setText(selectedIp)
            toggleIpListVisibility() // 隐藏列表
        }
    }
    
    private fun setupEventListeners() {
        startButton.setOnClickListener {
            if (!isSending) {
                val ip = ipEditText.text.toString()
                val portStr = portEditText.text.toString()
                
                if (ip.isEmpty()) {
                    Toast.makeText(this, "请输入IP地址", Toast.LENGTH_SHORT).show()
                    return@setOnClickListener
                }
                
                val port = portStr.toIntOrNull()
                if (port == null || port <= 0 || port > 65535) {
                    Toast.makeText(this, "请输入有效的端口号(1-65535)", Toast.LENGTH_SHORT).show()
                    return@setOnClickListener
                }
                
                startSending()
                statusTextView.text = "状态: 正在发送数据到 $ip:$port"
            }
        }
        
        stopButton.setOnClickListener {
            if (isSending) {
                stopSending()
                statusTextView.text = "状态: 已停止发送"
            }
        }
        
        discoverButton.setOnClickListener {
            discoverServices()
        }
        
        // 点击IP地址输入框时隐藏IP列表
        ipEditText.setOnClickListener {
            if (isIpListVisible) {
                toggleIpListVisibility()
            }
        }
    }
    
    private fun toggleIpListVisibility() {
        isIpListVisible = !isIpListVisible
        ipListView.visibility = if (isIpListVisible) View.VISIBLE else View.GONE
    }
    
    private fun initSensors() {
        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        linearAccelerationSensor = sensorManager.getDefaultSensor(Sensor.TYPE_LINEAR_ACCELERATION)
        rotationVectorSensor = sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)
        proximitySensor = sensorManager.getDefaultSensor(Sensor.TYPE_PROXIMITY)
        
        if (linearAccelerationSensor == null) {
            Toast.makeText(this, "设备不支持线性加速度传感器", Toast.LENGTH_LONG).show()
        }
        
        if (rotationVectorSensor == null) {
            Toast.makeText(this, "设备不支持旋转矢量传感器", Toast.LENGTH_LONG).show()
        }
        
        if (proximitySensor == null) {
            Toast.makeText(this, "设备不支持距离传感器", Toast.LENGTH_LONG).show()
        }
    }
    
    // 初始化网络服务发现
    private fun initNsd() {
        nsdManager = getSystemService(Context.NSD_SERVICE) as NsdManager
        initializeDiscoveryListener()
        initializeResolveListener()
    }
    
    // 初始化发现监听器
    private fun initializeDiscoveryListener() {
        discoveryListener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(regType: String) {
                runOnUiThread {
                    statusTextView.text = "状态: 正在发现服务..."
                    // 清空之前的发现结果
                    ipList.clear()
                    ipListAdapter.notifyDataSetChanged()
                }
            }

            override fun onServiceFound(service: NsdServiceInfo) {
                // 找到服务，尝试解析
                if (service.serviceType == "_midi._tcp.") {
                    nsdManager?.resolveService(service, resolveListener)
                }
            }

            override fun onServiceLost(service: NsdServiceInfo) {
                runOnUiThread {
                    statusTextView.text = "状态: 服务丢失"
                }
            }

            override fun onDiscoveryStopped(serviceType: String) {
                runOnUiThread {
                    statusTextView.text = "状态: 服务发现已停止"
                }
            }

            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                nsdManager?.stopServiceDiscovery(this)
                runOnUiThread {
                    statusTextView.text = "状态: 服务发现启动失败"
                }
            }

            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {
                nsdManager?.stopServiceDiscovery(this)
                runOnUiThread {
                    statusTextView.text = "状态: 停止服务发现失败"
                }
            }
        }
    }
    
    // 初始化解析监听器
    private fun initializeResolveListener() {
        resolveListener = object : NsdManager.ResolveListener {
            override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {
                runOnUiThread {
                    statusTextView.text = "状态: 解析服务失败，错误码: $errorCode"
                }
            }

            override fun onServiceResolved(serviceInfo: NsdServiceInfo) {
                val hostAddress = serviceInfo.host?.hostAddress
                val port = serviceInfo.port
                
                runOnUiThread {
                    if (hostAddress != null) {
                        val serviceEntry = "$hostAddress:$port"
                        // 避免重复添加
                        if (!ipList.contains(serviceEntry)) {
                            ipList.add(serviceEntry)
                            ipListAdapter.notifyDataSetChanged()
                            
                            // 如果是第一个发现的服务，自动填入
                            if (ipList.size == 1) {
                                ipEditText.setText(hostAddress)
                                portEditText.setText(port.toString())
                                statusTextView.text = "状态: 发现服务 $hostAddress:$port"
                                Toast.makeText(this@MainActivity, "已发现MIDI控制器服务", Toast.LENGTH_SHORT).show()
                            } else {
                                // 有多个服务时显示列表
                                statusTextView.text = "状态: 发现${ipList.size}个服务，点击输入框查看"
                                if (!isIpListVisible) {
                                    toggleIpListVisibility()
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    
    // 发现服务
    private fun discoverServices() {
        try {
            // 清空现有列表
            ipList.clear()
            ipListAdapter.notifyDataSetChanged()
            
            nsdManager?.discoverServices(
                "_midi._tcp.",
                NsdManager.PROTOCOL_DNS_SD,
                discoveryListener
            )
        } catch (e: Exception) {
            statusTextView.text = "状态: 发现服务时出错: ${e.message}"
        }
    }
    
    override fun onResume() {
        super.onResume()
        linearAccelerationSensor?.also { sensor ->
            sensorManager.registerListener(this, sensor, SensorManager.SENSOR_DELAY_GAME)
        }
        rotationVectorSensor?.also { sensor ->
            sensorManager.registerListener(this, sensor, SensorManager.SENSOR_DELAY_GAME)
        }
        proximitySensor?.also { sensor ->
            sensorManager.registerListener(this, sensor, SensorManager.SENSOR_DELAY_GAME)
        }
    }
    
    override fun onPause() {
        super.onPause()
        sensorManager.unregisterListener(this)
    }
    
    override fun onSensorChanged(event: SensorEvent?) {
        when (event?.sensor?.type) {
            Sensor.TYPE_LINEAR_ACCELERATION -> {
                // 保存线性加速度数据
                linearAccelData = event.values.clone()
                
                // 计算运动方向上的加速度（向量的模）
                cc1 = sqrt(
                    linearAccelData[0] * linearAccelData[0] +
                    linearAccelData[1] * linearAccelData[1] +
                    linearAccelData[2] * linearAccelData[2]
                )
            }
            
            Sensor.TYPE_ROTATION_VECTOR -> {
                // 保存旋转矢量数据
                rotationVectorData = event.values.clone()
                
                // 从旋转矢量获取旋转矩阵
                SensorManager.getRotationMatrixFromVector(rotationMatrix, rotationVectorData)
                
                // 从旋转矩阵获取方向角
                SensorManager.getOrientation(rotationMatrix, orientationAngles)
                
                // 计算手机平面与水平面的夹角（面与面的夹角）
                // 获取设备Z轴在世界坐标系中的方向（设备平面的法向量）
                val deviceZAxis = FloatArray(3)
                deviceZAxis[0] = rotationMatrix[6]  // Z轴X分量
                deviceZAxis[1] = rotationMatrix[7]  // Z轴Y分量
                deviceZAxis[2] = rotationMatrix[8]  // Z轴Z分量
                
                // 计算设备平面法向量与重力方向(0, 0, -1)的夹角
                // 由于重力方向是(0, 0, -1)，我们只需要考虑deviceZAxis的Z分量
                // 夹角 = acos(|Z分量|) ，因为重力方向单位向量是(0,0,-1)
                val dotProduct = kotlin.math.abs(deviceZAxis[2])  // 与(0,0,-1)的点积的绝对值
                val angleRadians = kotlin.math.acos(dotProduct.coerceIn(-1.0f, 1.0f))  // 限制在[-1,1]范围内防止数学错误
                cc11 = kotlin.math.abs(angleRadians * (180f / PI.toFloat()))  // 转换为角度并确保为正数
            }
            
            Sensor.TYPE_PROXIMITY -> {
                // 保存距离传感器数据
                proximityData = event.values[0]
                cc12 = proximityData
            }
        }
        
        updateUI()
    }
    
    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {
        // 不需要特殊处理
    }
    
    private fun updateUI() {
        // 更新原始数据显示
        val rawDataText = "线性加速度原始数据:\n" +
                "X: ${String.format("%.2f", linearAccelData[0])} m/s²\n" +
                "Y: ${String.format("%.2f", linearAccelData[1])} m/s²\n" +
                "Z: ${String.format("%.2f", linearAccelData[2])} m/s²\n\n" +
                "旋转矢量原始数据:\n" +
                "X: ${String.format("%.2f", rotationVectorData[0])}\n" +
                "Y: ${String.format("%.2f", rotationVectorData[1])}\n" +
                "Z: ${String.format("%.2f", rotationVectorData[2])}\n" +
                "Cos: ${String.format("%.2f", rotationVectorData[3])}\n" +
                "Heading: ${String.format("%.2f", rotationVectorData[4])}\n\n" +
                "距离传感器原始数据:\n" +
                "距离: ${String.format("%.2f", proximityData)} cm"
        
        rawDataTextView.text = rawDataText
        
        // 更新处理后数据显示
        val processedDataText = "处理后的数据:\n" +
                "运动方向加速度 (cc1): ${String.format("%.2f", cc1)} m/s²\n" +
                "平面与水平面夹角 (cc11): ${String.format("%.2f", cc11)}°\n" +
                "距离传感器数据 (cc12): ${String.format("%.2f", cc12)} cm"
        
        processedDataTextView.text = processedDataText
    }
    
    private fun startSending() {
        isSending = true
        handler.post(sendDataRunnable)
        startButton.isEnabled = false
        stopButton.isEnabled = true
    }
    
    private fun stopSending() {
        isSending = false
        handler.removeCallbacks(sendDataRunnable)
        startButton.isEnabled = true
        stopButton.isEnabled = false
    }
    
    private fun sendData() {
        thread {
            try {
                val ip = ipEditText.text.toString()
                val port = portEditText.text.toString().toIntOrNull() ?: return@thread
                
                if (datagramSocket == null || datagramSocket?.isClosed == true) {
                    datagramSocket = DatagramSocket()
                }
                
                val message = "$cc1 $cc11 $cc12"
                val buffer = message.toByteArray()
                val address = InetAddress.getByName(ip)
                val packet = DatagramPacket(buffer, buffer.size, address, port)
                datagramSocket?.send(packet)
            } catch (e: Exception) {
                e.printStackTrace()
                runOnUiThread {
                    Toast.makeText(this, "发送数据失败: ${e.message}", Toast.LENGTH_SHORT).show()
                }
            }
        }
    }
    
    override fun onDestroy() {
        super.onDestroy()
        stopSending()
        datagramSocket?.close()
        
        // 停止服务发现
        try {
            discoveryListener?.let { 
                nsdManager?.stopServiceDiscovery(it)
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }
}