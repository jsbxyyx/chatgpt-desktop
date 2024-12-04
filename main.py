import json
import os
import platform
import sqlite3
import sys
import traceback
from datetime import datetime
from functools import partial

import qdarktheme
from PySide6.QtCore import QTimer, QThread, Signal, Qt
from PySide6.QtWidgets import QMainWindow, QApplication, QHBoxLayout, QWidget, QVBoxLayout, QSplitter, QPushButton, \
    QListWidget, QTextEdit, QDialog, QLineEdit, QListWidgetItem, QMessageBox, QComboBox, QStyle
from openai import AzureOpenAI, OpenAI

from bubble_message import ChatWidget, BubbleMessage, MessageType
from toast import Toast
from tsid import TSID
from ui import main_ui, main_rc

___not_use = main_rc.qt_resource_name


def adapt_datetime_iso(date_time: datetime) -> str:
    """
    Convert a Python datetime.datetime into a timezone-naive ISO 8601 date string.
    >>> adapt_datetime_iso(datetime(2023, 4, 5, 6, 7, 8, 9))
    '2023-04-05T06:07:08.000009'
    """
    return date_time.isoformat()


def convert_timestamp(time_stamp: bytes) -> datetime:
    """
    Convert an ISO 8601 formatted bytestring to a datetime.datetime object.
    >>> convert_timestamp(b'2023-04-05T06:07:08.000009')
    datetime.datetime(2023, 4, 5, 6, 7, 8, 9)
    """
    return datetime.strptime(time_stamp.decode("utf-8"), "%Y-%m-%dT%H:%M:%S.%f")


sqlite3.register_adapter(datetime, adapt_datetime_iso)
sqlite3.register_converter("timestamp", convert_timestamp)

from loguru import logger

home_dir = os.path.expanduser('~')

logger.add(home_dir + "/chatgpt.log",
           rotation="50 MB",
           format="{time:YYYY-MM-DD HH:mm:ss.SSSZZ} | {level} | {message}",
           level="DEBUG")

os_name = platform.system().lower()
logger.debug(f"os: {os_name}")
if os_name == 'linux':
    # apt-get install libqt5gui5 libqt5widgets5 libqt5core5a
    # os.environ['QT_QPA_PLATFORM'] = 'xcb'
    os.environ['DISPLAY'] = ':0'
    if os.environ.get('QT_QPA_PLATFORM_PLUGIN_PATH', None) is not None:
        os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH")
    # os.environ['QT_DEBUG_PLUGINS'] = '1'
    pass


class WorkerThread(QThread):

    def __init__(self, parent=None, target=None, args=(), kwargs=None):
        QThread.__init__(self, parent)
        self._target = target
        self._args = args
        self._kwargs = {} if kwargs is None else kwargs

    def run(self) -> None:
        if self._target:
            try:
                self._target(*self._args, **self._kwargs)
            except Exception as e:
                logger.error(f'{traceback.format_exc()}')
                raise e


class MainWindow(QMainWindow):
    bubble_message_signal = Signal(dict)

    c_list_signal = Signal(str)

    chat_signal = Signal(str)

    def __init__(self):
        super(MainWindow, self).__init__()
        self.ui = main_ui.Ui_MainWindow()
        self.ui.setupUi(self)

        self.setWindowTitle("ChatGPT local")

        self.gpt_config = None
        self.conversation_id = None
        self.messages_array = []
        self.client = None
        self.db_file = home_dir + '/chatgpt_local.db'

        self.messages_comp = {}

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
        new_chat_button.clicked.connect(partial(self.init_new_chat, None))
        left_layout.addWidget(new_chat_button)

        self.c_list = QListWidget()
        self.c_list.doubleClicked.connect(self.c_list_double_clicked)
        left_layout.addWidget(self.c_list)

        c_list_tool = QWidget()
        c_list_tool_layout = QHBoxLayout(c_list_tool)
        c_list_tool_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        left_layout.addWidget(c_list_tool)

        delete_clist_button = QPushButton()
        delete_clist_button.setMaximumSize(24, 24)
        delete_clist_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        delete_clist_button.clicked.connect(self.delete_clist_button_clicked)
        c_list_tool_layout.addWidget(delete_clist_button)

        # 创建右侧布局：包含对话内容、输入框和发送按钮
        right_widget = QWidget()
        right_layout = QVBoxLayout()
        right_widget.setLayout(right_layout)

        self.model_field = QLineEdit()
        self.model_field.setText("gpt-4o")
        self.model_field.setPlaceholderText("请填写模型名称。例如：gpt-4o")
        right_layout.addWidget(self.model_field)

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

        self.bubble_message_signal.connect(self.bubble_message_update)
        self.c_list_signal.connect(self.c_list_update)
        self.chat_signal.connect(self.chat_update)

        self.init()

    def init(self):
        self.init_new_chat()
        self.init_client()

        self.init_database()
        self.init_c_list()

    def init_c_list(self):
        # fix: QThread: Destroyed while thread is still running
        self.wt = WorkerThread(target=self.fetch_c_list)
        self.wt.start()
        pass

    def delete_c_list(self, cid):
        logger.info(f"delete c_list: {cid}")
        if cid is not None:
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()
            try:
                sql = """
                        delete from chat_message where CID = ?
                        """
                c.execute(sql, (cid,))
                conn.commit()
                self.fetch_c_list()
                self.init_new_chat()
            except Exception as e:
                logger.error(f'{traceback.format_exc()}')
            finally:
                c.close()
                conn.close()

    def fetch_c_list(self):
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        try:
            sql = """
            select * from chat_message group by cid order by CREATETIME asc 
            """
            c.execute(sql)
            columns = [col[0] for col in c.description]
            data_ = [dict(zip(columns, row)) for row in c.fetchall()]
            self.c_list_signal.emit(json.dumps(data_))
        except Exception as e:
            logger.error(f'{traceback.format_exc()}')
        finally:
            c.close()
            conn.close()

    def fetch_chat(self, cid):
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        try:
            sql = """
                    select * from chat_message where CID = ? order by CREATETIME asc 
                    """
            c.execute(sql, (cid,))
            columns = [col[0] for col in c.description]
            data_ = [dict(zip(columns, row)) for row in c.fetchall()]
            self.chat_signal.emit(json.dumps({
                'cid': cid,
                'data': data_
            }))
        except Exception as e:
            logger.error(f'{traceback.format_exc()}')
        finally:
            c.close()
            conn.close()

    def init_database(self):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        try:
            sql = """
            create table if not exists chat_message (
                ID INTEGER PRIMARY KEY NOT NULL,
                CID TEXT NOT NULL,
                MID TEXT NOT NULL,
                CONTENT TEXT NOT NULL,
                SEND INTEGER NOT NULL,
                CREATETIME DATETIME NOT NULL
            )
            """
            cursor.execute(sql)
        except Exception as e:
            logger.error(f'{traceback.format_exc()}')
        finally:
            cursor.close()
            conn.close()

    def closeEvent(self, event):
        logger.info('close event')
        ret = QMessageBox.warning(self, '提示', '确认退出?',
                                  buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            QApplication.quit()
        else:
            event.ignore()
        pass

    def init_client(self):
        if self.gpt_config is None:
            json_data = self.read_gpt_config()
            if len(json_data.keys()) > 0:
                self.gpt_config = next(iter(json_data.values()))
        if self.gpt_config is not None:
            try:
                if self.gpt_config['type'] == 0:
                    self.client = AzureOpenAI(
                        api_key=self.gpt_config['key'],
                        azure_endpoint=self.gpt_config['endpoint'],
                        api_version='2024-02-01'
                    )
                else:
                    self.client = OpenAI(
                        api_key=self.gpt_config['key'],
                        base_url=self.gpt_config['endpoint'],
                    )
            except Exception as e:
                logger.error(f'{traceback.format_exc()}')
                Toast(message='配置错误', parent=self).show()

    def init_new_chat(self, conversation_id=None):
        logger.info('do new chat...')
        self.messages_array.clear()
        self.messages_array.append({"role": "system", "content": "你是一个很有用的助理."})
        self.messages_comp.clear()
        self.chat_content_widget.clear_message()

        if conversation_id is None:
            self.conversation_id = TSID.create().to_string()
            logger.info(f'new conversation id: {self.conversation_id}')
        else:
            self.conversation_id = conversation_id
            logger.info(f'choose conversation id: {self.conversation_id}')
        pass

    def do_config(self):
        logger.info('do config...')
        dialog = QDialog(self)
        dialog.setWindowTitle("配置信息")
        # dialog.setGeometry(150, 150, 300, 300)
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

        list_widget.addItems([])
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
        logger.info(f'add config...')
        dialog = QDialog(self)
        dialog.setWindowTitle("添加配置")
        dialog.setMinimumSize(400, 300)
        # dialog.setGeometry(150, 150, 300, 300)
        layout = QVBoxLayout(dialog)

        name = QLineEdit()
        name.setPlaceholderText("别名:需要唯一,否则会出现覆盖配置情况")
        name.setText('' if config.get('name', None) is None else config.get('name'))
        layout.addWidget(name)

        type_combo = QComboBox()
        type_combo.addItem("Azure")
        type_combo.addItem("通用")
        layout.addWidget(type_combo)

        endpoint = QLineEdit()
        endpoint.setPlaceholderText("OPENAI_ENDPOINT")
        endpoint.setText('' if config.get('endpoint', None) is None else config.get('endpoint'))
        layout.addWidget(endpoint)

        key = QLineEdit()
        key.setPlaceholderText("OPENAI_KEY")
        key.setText('' if config.get('key', None) is None else config.get('key'))
        layout.addWidget(key)

        ok_button = QPushButton("保存")
        ok_button.clicked.connect(partial(self.add_config, dialog, name, type_combo, endpoint, key, list_widget))
        layout.addWidget(ok_button)

        dialog.exec()
        pass

    def add_config(self, dialog: QDialog, name_q: QLineEdit, type_q: QComboBox, endpoint_q: QLineEdit, key_q: QLineEdit,
                   list_widget: QListWidget):
        name = name_q.text()
        endpoint = endpoint_q.text()
        key = key_q.text()
        logger.info(f'add config : {name}')

        if name is None or name.strip() == '' \
                or endpoint is None or endpoint.strip() == '' \
                or key is None or key.strip() == '':
            Toast(message="请填写配置", parent=dialog).show()
            return

        json_data = self.read_gpt_config()
        json_data[name] = {
            'name': name,
            'type': type_q.currentIndex(),
            'endpoint': endpoint,
            'key': key,
        }
        self.write_gpt_config(json_data)

        dialog.close()

        self.refresh_config(self, list_widget)

    def choose_config(self, dialog: QDialog, list_widget: QListWidget):
        item = list_widget.currentItem()
        if item is None:
            Toast(message='请选择配置', parent=dialog).show()
            return
        key = item.text()
        logger.info(f'choose config : {key}')

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
        if item is None:
            Toast(message='请选择配置', parent=parent).show()
            return
        ret = QMessageBox.warning(parent, '提示', '确认删除?',
                                  buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            key = item.text()
            logger.info(f'delete config : {key}')
            json_data = self.read_gpt_config()
            del json_data[key]
            self.write_gpt_config(json_data)
            self.refresh_config(parent, list_widget)
        pass

    def send_message(self):
        model = self.get_model()
        if model is None or model.strip() == '':
            Toast(message="请填写模型名称", parent=self).show()
            return
        message_text = self.input_field.toPlainText()
        if message_text:
            input_mid = TSID.create().to_string()
            self.add_message(message_text, is_send=True, mid=input_mid)
            self.input_field.clear()

            self.insert_message_to_db(input_mid, message_text, 1)

            logger.debug(f'问题:{message_text}')
            self.messages_array.append({"role": "user", "content": message_text})
            if self.client is None:
                Toast(message='请选择配置', parent=self).show()
                return

            if len(self.messages_array) < 3:
                self.init_c_list()

            self.wt = WorkerThread(target=self.chat_completions, args=(model,))
            self.wt.start()

    def add_message(self, message, is_send=True, mid=''):
        avatar = ':ui/avatar.png' if is_send else ':ui/icon.png'

        if message is None:
            message = ''

        message_comp = self.messages_comp.get(mid, None)
        if message_comp is None:
            message_comp = BubbleMessage(message, avatar, Type=MessageType.Text, is_send=is_send)
            self.chat_content_widget.add_message_item(message_comp)
            self.messages_comp[mid] = message_comp
        else:
            message_comp.append_text(message)

        QTimer.singleShot(100, self.scroll_to_bottom)

    def get_model(self):
        return self.model_field.text()

    def chat_completions(self, model):
        completion = self.client.chat.completions.create(
            model=model,
            messages=self.messages_array,
            stream=True
        )
        generated_text = ''
        mid = None
        for chunk in completion:
            if len(chunk.choices) > 0:
                chunk_text = chunk.choices[0].delta.content
                if chunk_text is None:
                    chunk_text = ''
                generated_text += chunk_text
                self.bubble_message_signal.emit({
                    'text': chunk_text,
                    'is_send': False,
                    'mid': chunk.id,
                })
                if mid is None:
                    mid = chunk.id
        if mid is not None:
            self.messages_array.append({"role": "assistant", "content": generated_text})
            logger.debug(f'回答：{generated_text}')

        self.insert_message_to_db(mid, generated_text, 0)

    def insert_message_to_db(self, mid, content, send):
        if mid is not None:
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()
            try:
                sql = """insert into chat_message(ID, CID, MID, CONTENT, SEND, CREATETIME) values (?,?,?,?,?,?)"""
                c.execute(sql, (TSID.create().number, self.conversation_id, mid, content, send, datetime.now()))
                conn.commit()
            except Exception as e:
                logger.error(f'{traceback.format_exc()}')
            finally:
                c.close()
                conn.close()

    def delete_clist_button_clicked(self):
        item = self.c_list.currentItem()
        if item is None:
            Toast(message="请选择要删除的对话", parent=self).show()
            return
        ret = QMessageBox.warning(self, '提示', f'确认删除对话【{item.text()}】?',
                                  buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            cid = item.data(QListWidgetItem.ItemType.UserType)
            self.wt = WorkerThread(target=self.delete_c_list, args=(cid,))
            self.wt.start()

    def c_list_double_clicked(self, qModelIndex):
        item = self.c_list.item(qModelIndex.row())
        cid = item.data(QListWidgetItem.ItemType.UserType)
        logger.info(f'c_list double clicked : {cid}')
        self.wt = WorkerThread(target=self.fetch_chat, args=(cid,))
        self.wt.start()
        pass

    def chat_update(self, data: str):
        result = json.loads(data)
        cid = result['cid']
        data_ = result['data']
        logger.info(f'chat update : {cid}')
        self.init_new_chat(cid)
        for row in data_:
            send = row['SEND']
            content = row['CONTENT']
            mid = row['MID']
            self.add_message(content, is_send=True if send == 1 else False, mid=mid)
            if send == 1:
                self.messages_array.append({"role": "user", "content": content})
            else:
                self.messages_array.append({"role": "assistant", "content": content})
        pass

    def c_list_update(self, data: str):
        result = json.loads(data)
        self.c_list.clear()
        for row in result:
            cid = row['CID']
            content = row['CONTENT'][0: 20]
            item = QListWidgetItem()
            item.setText(content)
            item.setData(QListWidgetItem.ItemType.UserType, cid)
            self.c_list.addItem(item)
        pass

    def bubble_message_update(self, data: dict):
        text = data['text']
        is_send = data['is_send']
        mid = data['mid']
        self.add_message(text, is_send, mid)

    def scroll_to_bottom(self):
        self.chat_content_widget.set_scroll_bar_last()

    def read_gpt_config(self):
        json_data = {}
        config_path = home_dir + "/chatgpt_local.config"
        if not os.path.exists(config_path):
            self.write_gpt_config({})
            return json_data
        with open(config_path, 'r+', encoding='utf-8') as f:
            content = f.read()
            if content.strip() == '':
                content = '{}'
            json_data = json.loads(content)
        return json_data

    def write_gpt_config(self, config):
        config_path = home_dir + "/chatgpt_local.config"
        with open(config_path, 'w+', encoding='utf-8') as f:
            f.write(json.dumps(config))


if __name__ == '__main__':
    app = QApplication(sys.argv)
    qdarktheme.setup_theme(theme="light")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
    pass
