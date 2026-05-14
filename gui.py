#!/usr/bin/env python3
import sys
import json
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QSpinBox, QSlider, QPushButton,
    QGroupBox, QFormLayout, QMessageBox, QLineEdit
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QPen, QColor, QFont
import paho.mqtt.client as mqtt

class WaveformPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.freq = 1000
        self.duty = 50
        self.setMinimumHeight(180)
        self.setStyleSheet("background-color: #1e1e28;")

    def update_params(self, freq: int, duty: int):
        self.freq = max(1, freq)
        self.duty = duty
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        margin = 40

        painter.fillRect(0, 0, w, h, QColor(30, 30, 40))

        if self.freq <= 0:
            return

        period_us = 1_000_000 / self.freq
        high_us = period_us * (self.duty / 100.0)
        low_us = period_us - high_us

        scale_x = (w - 2 * margin) / (2 * period_us)

        painter.setPen(QPen(QColor(0, 255, 120), 3))
        x = margin
        y_high = margin + 25
        y_low = h - margin - 25

        period_count = 2

        for period in range(period_count):
            high_end_x = x + high_us * scale_x
            low_end_x_temp = high_end_x + low_us * scale_x
            painter.drawLine(int(x), y_high, int(high_end_x), y_high)
            painter.drawLine(int(high_end_x), y_high, int(high_end_x), y_low)
            painter.drawLine(int(high_end_x), y_low, int(low_end_x_temp), y_low)
            painter.drawLine(int(low_end_x_temp), y_low, int(low_end_x_temp), y_high)

            if period == 0:
                high_center_x = x + (high_us * scale_x) / 2
                high_text = f"{high_us:.1f} µs"
                text_width = painter.fontMetrics().horizontalAdvance(high_text)
                painter.drawText(int(x + (high_us * scale_x) / 2 - text_width / 2), y_high - 12, high_text)
            elif period == period_count - 1:
                low_text = f"{low_us:.1f} µs"
                text_width = painter.fontMetrics().horizontalAdvance(low_text)
                painter.drawText(int(high_end_x + (low_us * scale_x) / 2 - text_width / 2), y_high - 12, low_text)

            x = low_end_x_temp

        painter.setPen(QPen(Qt.GlobalColor.white))
        painter.setFont(QFont("Arial", 10, QFont.Weight.Bold))

        painter.setFont(QFont("Arial", 9))
        painter.drawText(margin, h - 10, f"Period: {period_us:.1f} µs   |   Frequency: {self.freq} Hz")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("behead yourself!!!")
        self.resize(850, 650)

        self.mqtt_client = None
        self.broker_ip = "127.0.0.1"

        self.init_ui()
        self.setup_mqtt()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        broker_group = QGroupBox("MQTT Broker")
        broker_layout = QHBoxLayout()
        self.broker_edit = QLineEdit(self.broker_ip)
        self.broker_edit.setFixedWidth(150)
        reconnect_btn = QPushButton("Connect")
        reconnect_btn.clicked.connect(self.reconnect_mqtt)
        broker_layout.addWidget(QLabel("Broker IP:"))
        broker_layout.addWidget(self.broker_edit)
        broker_layout.addWidget(reconnect_btn)
        broker_group.setLayout(broker_layout)
        main_layout.addWidget(broker_group)

        ctrl_group = QGroupBox("Signal Parameters")
        form = QFormLayout()

        self.gpio_combo = QComboBox()
        self.gpio_combo.addItems(["144 (PE16)", "143 (PE15)", "140 (PE12)"])

        self.freq_spin = QSpinBox()
        self.freq_spin.setRange(1, 100000)
        self.freq_spin.setValue(1000)
        self.freq_spin.setSuffix(" Hz")

        self.duty_slider = QSlider(Qt.Orientation.Horizontal)
        self.duty_slider.setRange(1, 99)
        self.duty_slider.setValue(50)
        self.duty_label = QLabel("50%")
        self.duty_slider.valueChanged.connect(lambda v: self.duty_label.setText(f"{v}%"))

        form.addRow("GPIO Line:", self.gpio_combo)
        form.addRow("Frequency:", self.freq_spin)
        form.addRow("Duty Cycle:", self.duty_slider)
        form.addRow("", self.duty_label)

        ctrl_group.setLayout(form)
        main_layout.addWidget(ctrl_group)

        preview_group = QGroupBox("Signal Preview")
        preview_layout = QVBoxLayout()
        self.preview = WaveformPreview()
        preview_layout.addWidget(self.preview)
        preview_group.setLayout(preview_layout)
        main_layout.addWidget(preview_group)

        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.start_btn.clicked.connect(self.send_start)
        self.stop_btn.clicked.connect(self.send_stop)

        self.start_btn.setStyleSheet("background-color: #006600; color: white; font-weight: bold;")
        self.stop_btn.setStyleSheet("background-color: #880000; color: white; font-weight: bold;")

        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)
        main_layout.addLayout(btn_layout)

        self.status_label = QLabel("Connecting...")
        self.status_label.setStyleSheet("color: orange;")
        main_layout.addWidget(self.status_label)

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_preview)
        self.timer.start(150)

    def setup_mqtt(self):
        if self.mqtt_client:
            self.mqtt_client.disconnect()

        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_disconnect = self.on_disconnect

        try:
            self.status_label.setText(f"Connecting to {self.broker_ip}...")
            self.mqtt_client.connect(self.broker_ip, 1883, 60)
            self.mqtt_client.loop_start()
        except Exception as e:
            self.status_label.setText(f"Connection failed: {e}")
            self.status_label.setStyleSheet("color: red;")

    def reconnect_mqtt(self):
        self.broker_ip = self.broker_edit.text().strip()
        self.setup_mqtt()

    def on_connect(self, client, userdata, flags, rc):
        self.status_label.setText(f"Connected to {self.broker_ip}")
        self.status_label.setStyleSheet("color: lime;")

    def on_disconnect(self, client, userdata, rc):
        self.status_label.setText("Disconnected from broker")
        self.status_label.setStyleSheet("color: red;")

    def update_preview(self):
        self.preview.update_params(self.freq_spin.value(), self.duty_slider.value())

    def send_start(self):
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            QMessageBox.warning(self, "Not Connected", "Connect to MQTT broker first.")
            return

        try:
            gpio_line = int(self.gpio_combo.currentText().split()[0])

            cmd = {
                "action": "start",
                "gpio_line": gpio_line,
                "freq_hz": self.freq_spin.value(),
                "duty_percent": self.duty_slider.value()
            }

            self.mqtt_client.publish("gpio/control", json.dumps(cmd), qos=1)
            print(f"Sent START: {cmd}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def send_stop(self):
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            return
        cmd = {"action": "stop"}
        self.mqtt_client.publish("gpio/control", json.dumps(cmd), qos=1)
        print("Sent STOP")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
