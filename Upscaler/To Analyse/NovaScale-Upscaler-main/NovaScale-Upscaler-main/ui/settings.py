from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QSlider, QComboBox, QGroupBox, QCheckBox, QPushButton)
from PySide6.QtCore import Qt, Signal

class SettingsPanel(QWidget):
    config_changed = Signal(dict)

    def __init__(self):
        super().__init__()
        self.init_ui()

        self.apply_styles()

    def apply_styles(self):
        self.setStyleSheet("""
            QGroupBox { 
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid #1a1a1f; 
                border-radius: 10px; 
                margin-top: 15px; 
                font-weight: bold; 
                color: #555; 
                padding: 15px;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
            QLabel { color: #888; font-family: 'Inter'; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; }
            QComboBox { 
                background: #121216; color: #eee; border: 1px solid #222; border-radius: 6px; padding: 8px; 
                font-weight: bold;
            }
            QComboBox::drop-down { border: none; }
            QSlider::groove:horizontal { background: #1a1a1f; height: 4px; border-radius: 2px; }
            QSlider::handle:horizontal { 
                background: #00f2ff; border-radius: 7px; width: 14px; height: 14px; margin-top: -5px; 
            }
            QCheckBox { color: #ccc; spacing: 10px; font-weight: bold; }
            QCheckBox::indicator { width: 18px; height: 18px; border: 2px solid #333; border-radius: 4px; }
            QCheckBox::indicator:checked { background: #00f2ff; border-color: #00f2ff; }
        """)

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(15)

        # Mode Selection
        mode_group = QGroupBox("PRESET")
        mode_layout = QVBoxLayout(mode_group)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["SPATIAL RECONSTRUCTION", "BALANCED ACCURACY", "CNN ULTRA (EXPERIMENTAL)"])
        mode_layout.addWidget(self.mode_combo)
        layout.addWidget(mode_group)

        # Scale Factor
        scale_group = QGroupBox("UPSCALING FACTOR")
        scale_layout = QVBoxLayout(scale_group)
        self.scale_label = QLabel("1.50X SCALE")
        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(12, 20)
        self.scale_slider.setValue(15)
        self.scale_slider.valueChanged.connect(lambda v: self.scale_label.setText(f"{v/10.0:.2f}X SCALE"))
        scale_layout.addWidget(self.scale_label)
        scale_layout.addWidget(self.scale_slider)
        layout.addWidget(scale_group)

        # Sharpening
        sharp_group = QGroupBox("RECONSTRUCTION FILTER")
        sharp_layout = QVBoxLayout(sharp_group)
        self.sharp_label = QLabel("SHARPNESS: 50%")
        self.sharp_slider = QSlider(Qt.Horizontal)
        self.sharp_slider.setRange(0, 100)
        self.sharp_slider.setValue(50)
        self.sharp_slider.valueChanged.connect(lambda v: self.sharp_label.setText(f"SHARPNESS: {v}%"))
        sharp_layout.addWidget(self.sharp_label)
        sharp_layout.addWidget(self.sharp_slider)
        
        self.temporal_check = QCheckBox("TEMPORAL STABILIZATION")
        sharp_layout.addWidget(self.temporal_check)
        layout.addWidget(sharp_group)

        layout.addStretch()
