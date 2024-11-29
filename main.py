import json
import os
import platform
import sys
from functools import partial

import qdarktheme
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow, QApplication, QHBoxLayout, QWidget, QVBoxLayout, QSplitter, QPushButton, \
    QListWidget, QTextEdit, QDialog, QLineEdit, QMessageBox
from openai import AzureOpenAI

from bubble_message import ChatWidget, BubbleMessage, MessageType
from toast import Toast
from ui import main_ui

os_name = platform.system().lower()
print(f"os: {os_name}")
if os_name == 'linux':
    # apt-get install libqt5gui5 libqt5widgets5 libqt5core5a
    # os.environ['QT_QPA_PLATFORM'] = 'xcb'
    os.environ['DISPLAY'] = ':0'
    if os.environ.get('QT_QPA_PLATFORM_PLUGIN_PATH', None) is not None:
        os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH")
    # os.environ['QT_DEBUG_PLUGINS'] = '1'
    pass


class MainWindow(QMainWindow):

    def __init__(self):
        super(MainWindow, self).__init__()
        self.ui = main_ui.Ui_MainWindow()
        self.ui.setupUi(self)

        self.setWindowTitle("ChatGPT local")

        self.gpt_config = None
        self.messages_array = []
        self.do_new_chat()
        self.client = None
        self.init_client()

        tool_bar = self.addToolBar("toolBar")
        tool_bar.setMovable(False)
        tool_bar.setFloatable(False)

        push_button_config = QPushButton("配置")
        push_button_config.clicked.connect(self.do_config)
        tool_bar.addWidget(push_button_config)

        # 创建主部件和主布局
        main_widget = QWidget()
        main_layout = QHBoxLayout(main_widget)

        # 创建左侧布局：包含新对话按钮和联系人列表
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        new_chat_button = QPushButton("新对话")
        new_chat_button.clicked.connect(self.do_new_chat)
        left_layout.addWidget(new_chat_button)

        contact_list = QListWidget()
        left_layout.addWidget(contact_list)

        # 创建右侧布局：包含对话内容、输入框和发送按钮
        right_widget = QWidget()
        right_layout = QVBoxLayout()
        right_widget.setLayout(right_layout)

        self.chat_content_widget = ChatWidget()
        right_layout.addWidget(self.chat_content_widget)

        input_layout = QHBoxLayout()

        self.input_field = QTextEdit()
        self.input_field.setFixedHeight(50)  # 固定高度模拟单行输入
        self.input_field.setStyleSheet(""" 
        QTextEdit { border: 1px solid gray; padding: 3px; background: white; font: 14px; } 
        """)
        send_button = QPushButton("发送")
        send_button.setFixedHeight(self.input_field.height())
        send_button.clicked.connect(self.send_message)

        input_layout.addWidget(self.input_field)
        input_layout.addWidget(send_button)

        right_layout.addLayout(input_layout)

        # 使用 QSplitter 创建可调整大小的布局
        splitter = QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([300, 500])

        main_layout.addWidget(splitter)

        # 设置主部件
        self.setCentralWidget(main_widget)

    def closeEvent(self, event):
        print('close event')
        QApplication.quit()
        pass

    def init_client(self):
        if self.gpt_config is None:
            json_data = self.read_gpt_config()
            if len(json_data.keys()) > 0:
                self.gpt_config = next(iter(json_data.values()))
        if self.gpt_config is not None:
            self.client = AzureOpenAI(
                api_key=self.gpt_config['key'],
                azure_endpoint=self.gpt_config['endpoint'],
                api_version='2024-02-01'
            )

    def do_new_chat(self):
        print(f'do new chat...')
        self.messages_array.clear()
        self.messages_array.append({"role": "system", "content": "你是一个很有用的助理."})
        pass

    def do_config(self):
        print(f'do config...')
        dialog = QDialog(self)
        dialog.setWindowTitle("配置信息")
        dialog.setGeometry(150, 150, 300, 300)
        layout = QVBoxLayout(dialog)

        list_widget = QListWidget()

        add_button = QPushButton("添加配置")
        add_button.clicked.connect(partial(self.add_config_ui, dialog, list_widget))
        layout.addWidget(add_button)

        refresh_button = QPushButton("刷新配置")
        add_button.clicked.connect(partial(self.refresh_config, dialog, list_widget))
        layout.addWidget(refresh_button)

        del_button = QPushButton("查看")
        del_button.clicked.connect(partial(self.view_config, dialog, list_widget))
        layout.addWidget(del_button)

        del_button = QPushButton("删除")
        del_button.clicked.connect(partial(self.del_config, dialog, list_widget))
        layout.addWidget(del_button)

        items = []
        list_widget.addItems(items)
        layout.addWidget(list_widget)

        self.refresh_config(self, list_widget)

        ok_button = QPushButton("选择")
        ok_button.clicked.connect(partial(self.choose_config, dialog, list_widget))
        layout.addWidget(ok_button)

        dialog.exec()
        pass

    def refresh_config(self, parent: QDialog, list_widget: QListWidget):
        list_widget.clear()
        json_data = self.read_gpt_config()
        for key, value in json_data.items():
            list_widget.addItem(key)

    def view_config(self, parent: QDialog, list_widget: QListWidget):
        item = list_widget.currentItem()
        json_data = self.read_gpt_config()
        if item is not None:
            self.add_config_ui(parent, list_widget, json_data.get(item.text(), {}))
        else:
            Toast(message="请选择配置", parent=parent).show()
            return
        pass

    def add_config_ui(self, parent: QDialog, list_widget: QListWidget, config={}):
        print(f'add config...')
        dialog = QDialog(self)
        dialog.setWindowTitle("添加配置")
        dialog.setGeometry(150, 150, 300, 300)
        layout = QVBoxLayout(dialog)

        name = QLineEdit()
        name.setPlaceholderText("别名:需要唯一,否则会出现覆盖配置情况")
        name.setText('' if config.get('name', None) is None else config.get('name'))
        layout.addWidget(name)

        endpoint = QLineEdit()
        endpoint.setPlaceholderText("AZURE_OPENAI_ENDPOINT")
        endpoint.setText('' if config.get('endpoint', None) is None else config.get('endpoint'))
        layout.addWidget(endpoint)

        key = QLineEdit()
        key.setPlaceholderText("AZURE_OPENAI_KEY")
        key.setText('' if config.get('key', None) is None else config.get('key'))
        layout.addWidget(key)

        ok_button = QPushButton("保存")
        ok_button.clicked.connect(partial(self.add_config, dialog, name, endpoint, key, list_widget))
        layout.addWidget(ok_button)

        dialog.exec()
        pass

    def add_config(self, dialog: QDialog, name_q: QLineEdit, endpoint_q: QLineEdit, key_q: QLineEdit,
                   list_widget: QListWidget):
        name = name_q.text()
        endpoint = endpoint_q.text()
        key = key_q.text()
        print(f'add config : {name} {endpoint} {key}')

        if name is None or name.strip() == '' \
                or endpoint is None or endpoint.strip() == '' \
                or key is None or key.strip() == '':
            Toast(message="请填写配置", parent=dialog).show()
            return

        json_data = self.read_gpt_config()
        json_data[name] = {
            'name': name,
            'endpoint': endpoint,
            'key': key,
        }
        self.write_gpt_config(json_data)

        dialog.close()

        self.refresh_config(list_widget)

    def choose_config(self, dialog: QDialog, list_widget: QListWidget):
        item = list_widget.currentItem()
        key = item.text()
        print(f"choose config : {key}")

        json_data = self.read_gpt_config()
        gpt_config = json_data[key]

        if gpt_config is None:
            Toast(message="配置文件未找到", parent=dialog).show()
            return

        self.gpt_config = gpt_config

        self.init_client()
        dialog.close()
        pass

    def del_config(self, parent: QDialog, list_widget: QListWidget):
        item = list_widget.currentItem()
        key = item.text()
        print(f"delete config : {key}")
        json_data = self.read_gpt_config()
        del json_data[key]
        self.write_gpt_config(json_data)
        self.refresh_config(list_widget)
        pass

    def send_message(self):
        message_text = self.input_field.toPlainText()
        if message_text:
            self.add_message(message_text, is_send=True)
            self.input_field.clear()

            self.messages_array.append({"role": "user", "content": message_text})
            if self.client is None:
                Toast(message='请选择配置', parent=self).show();
                return
            response = self.client.chat.completions.create(
                model='gpt-4o',
                messages=self.messages_array,
                stream=False
            )
            if response.choices:
                generated_text = response.choices[0].message.content
                self.add_message(generated_text, is_send=False)
                self.messages_array.append({"role": "assistant", "content": generated_text})
            else:
                Toast(message=response.error, parent=self).show()


    def add_message(self, message, is_send=True):
        avatar = 'ui/icon.png' if is_send else 'ui/icon.png'
        message_comp = BubbleMessage(message, avatar, Type=MessageType.Text, is_send=is_send)
        self.chat_content_widget.add_message_item(message_comp)

        QTimer.singleShot(100, self.scroll_to_bottom)

    def scroll_to_bottom(self):
        self.chat_content_widget.set_scroll_bar_last()

    def read_gpt_config(self):
        config_path = os.getcwd() + "/.gptconfig"
        json_data = {}
        with open(config_path, 'r+', encoding='utf-8') as f:
            content = f.read()
            if content.strip() == '':
                content = '{}'
            json_data = json.loads(content)
        return json_data

    def write_gpt_config(self, config):
        config_path = os.getcwd() + "/.gptconfig"
        with open(config_path, 'w+', encoding='utf-8') as f:
            f.write(json.dumps(config))


if __name__ == '__main__':
    app = QApplication(sys.argv)
    qdarktheme.setup_theme("light")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
    pass
