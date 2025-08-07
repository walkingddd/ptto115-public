import os
import time
import requests
from dotenv import load_dotenv
from p115client.client import P115Client
from p115client.tool.upload import multipart_upload_init

# 加载.env文件中的环境变量
load_dotenv()

# ======================== 环境变量配置（从.env文件读取） ========================
# 115客户端配置
version = "1.0.4"
TG_BOT_TOKEN = os.getenv("ENV_TG_BOT_TOKEN", "")
TG_ADMIN_USER_ID = int(os.getenv("ENV_TG_ADMIN_USER_ID", "0"))
TRY_MAX_COUNT = int(os.getenv("ENV_TRY_MAX_COUNT", "999999"))
try:
    # 读取115 cookies
    COOKIES = os.getenv("ENV_115_COOKIES", "")

    # 读取上传目标目录ID
    UPLOAD_TARGET_PID = int(os.getenv("ENV_115_UPLOAD_PID", "0"))

except (ValueError, TypeError) as e:
    # 环境变量值格式错误或未设置
    print(f"环境变量错误：{e}")
    print("请确保.env文件中已正确设置所有必要的环境变量")
    # 终止程序，因为缺少必要的环境变量
    exit(1)

# ======================== 其他固定配置 ========================
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "upload")  # 待上传目录
SLEEP_AFTER_FILE = 10  # 单个文件处理后休眠（秒）
SLEEP_AFTER_ROUND = 60  # 一轮遍历后休眠（秒）


# ======================== 工具函数 ========================
def check_file_size_stability(file_path, check_interval=30, max_attempts=1000):
    """检查文件大小稳定性，防止文件不完整"""
    for attempt in range(max_attempts):
        size1 = os.path.getsize(file_path)
        time.sleep(check_interval)
        size2 = os.path.getsize(file_path)
        if size1 == size2:
            print(f"[信息] 文件大小稳定：{file_path}")
            return True
        print(f"[警告] 文件大小不稳定，第 {attempt + 1} 次检查：{file_path}")
    print(f"[错误] 文件大小不稳定，放弃上传：{file_path}")
    return False


def init_115_client():
    """初始化115客户端（cookies认证）"""
    try:
        client = P115Client(COOKIES)
        print("[信息] 客户端初始化成功（cookies有效）")
        return client
    except Exception as e:
        print(f"[错误] 客户端初始化失败（检查cookies是否有效）：{e}")
        raise

class TelegramNotifier:
    def __init__(self, bot_token, user_id):
        self.bot_token = bot_token
        self.user_id = user_id
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}/" if self.bot_token else None

    def send_message(self, message):
        """向指定用户发送消息，若bot_token未设置则跳过发送"""
        # 检查bot_token是否存在
        if not self.bot_token:
            print("未设置bot_token，跳过发送消息")
            return False

        if not message:
            print("警告：消息内容不能为空")
            return False

        success_count = 0
        fail_count = 0

        params = {
            "chat_id": self.user_id,
            "text": message
        }

        try:
            response = requests.get(f"{self.base_url}sendMessage", params=params)
            response.raise_for_status()

            result = response.json()
            if result.get("ok", False):
                print(f"消息已成功发送给用户 {self.user_id}")
                success_count += 1
            else:
                print(f"发送消息给用户 {self.user_id} 失败: {result.get('description', '未知错误')}")
                fail_count += 1

        except requests.exceptions.RequestException as e:
            print(f"发送消息给用户 {self.user_id} 时发生错误: {str(e)}")
            fail_count += 1

        print(f"消息发送完成 - 成功: {success_count}, 失败: {fail_count}")
        return success_count > 0

# ======================== 核心逻辑 ========================
def main():
    # 初始化Telegram通知器
    notifier = TelegramNotifier(TG_BOT_TOKEN, TG_ADMIN_USER_ID)
    
    # 发送启动通知（如果配置了Telegram）
    if TG_BOT_TOKEN and TG_ADMIN_USER_ID:
        notifier.send_message(f"ptto115：开始监控待上传目录，当前版本 {version}")
    cache = {}  # 内存缓存：{文件绝对路径: SHA1}
    attempt_count = {}  # 跟踪每个文件的尝试次数：{文件绝对路径: 次数}
    client = init_115_client()
    last_delete_time = time.time()
    
    # 确保transfer目录存在
    transfer_dir = os.path.join(os.path.dirname(__file__), "transfer")
    if not os.path.exists(transfer_dir):
        os.makedirs(transfer_dir)
        print(f"[信息] 创建transfer目录：{transfer_dir}")

    while True:
        print(f"[信息] 开始遍历待上传目录，当前版本 {version}...")
        # 遍历upload目录文件
        for root, _, files in os.walk(UPLOAD_DIR):
            for filename in files:
                file_path = os.path.join(root, filename)
                file_key = file_path

                print(f"[信息] 正在检查文件 {file_path} 的大小稳定性...")
                # 检查文件大小稳定性
                if not check_file_size_stability(file_path):
                    continue

                # 获取文件大小
                try:
                    filesize = os.path.getsize(file_path)
                    print(f"[信息] 获取到文件 {file_path} 的大小为 {filesize} 字节")
                except FileNotFoundError:
                    print(f"[信息] 文件已删除：{file_path}")
                    if file_key in cache:
                        del cache[file_key]
                    continue

                # 检查缓存中是否有哈希值
                cached_sha1 = cache.get(file_key)
                if cached_sha1:
                    print(f"[信息] 使用缓存的SHA1值：{file_path} → {cached_sha1}")
                else:
                    print(f"[信息] 缓存中无SHA1值，将通过上传接口自动计算")

                # 初始化文件尝试次数
                if file_key not in attempt_count:
                    attempt_count[file_key] = 0
                
                # 增加尝试次数
                attempt_count[file_key] += 1
                print(f"[信息] 正在尝试上传文件（第 {attempt_count[file_key]}/{TRY_MAX_COUNT} 次）：{file_path}")
                
                # 检查是否达到最大尝试次数
                if attempt_count[file_key] > TRY_MAX_COUNT:
                    # 移动文件到transfer目录
                    transfer_path = os.path.join(os.path.dirname(__file__), "transfer", filename)
                    try:
                        os.rename(file_path, transfer_path)
                        print(f"[信息] 已将文件移动到transfer目录：{file_path} -> {transfer_path}")
                        # 发送失败通知（如果配置了Telegram）
                        if TG_BOT_TOKEN and TG_ADMIN_USER_ID:
                            notifier.send_message(f"ptto115：文件“{filename}”尝试上传 {TRY_MAX_COUNT} 次失败，已移动到transfer目录")
                        if file_key in cache:
                            del cache[file_key]
                        if file_key in attempt_count:
                            del attempt_count[file_key]
                    except Exception as e:
                        print(f"[错误] 移动文件到transfer目录失败：{e}")
                    continue
                
                # 调用秒传接口（使用环境变量配置的PID）
                try:
                    upload_result = multipart_upload_init(
                        client=client,
                        path=file_path,
                        filename=filename,
                        filesize=filesize,
                        filesha1=cached_sha1 or '',  # 使用缓存的哈希值或留空让接口自动计算
                        pid=UPLOAD_TARGET_PID
                    )

                    # 处理秒传结果
                    if "status" in upload_result:
                        print(f"[成功] 秒传成功：{file_path}（目标目录ID：{UPLOAD_TARGET_PID}）")
                        # 发送成功通知（如果配置了Telegram）
                        if TG_BOT_TOKEN and TG_ADMIN_USER_ID:
                            notifier.send_message(f"ptto115：文件“{filename}”秒传成功")
                        os.remove(file_path)
                        print(f"[信息] 已删除本地文件：{file_path}")
                        if file_key in cache:
                            del cache[file_key]
                        if file_key in attempt_count:
                            del attempt_count[file_key]
                    else:
                        print(f"[失败] 秒传未成功：{file_path}，从上传配置信息里获取哈希值并缓存")
                        # 从上传配置信息里获取哈希值
                        filesha1 = upload_result.get('filesha1', '')
                        if filesha1:
                            cache[file_key] = filesha1
                            print(f"[信息] 已缓存文件哈希值：{file_path} → {filesha1}")

                except Exception as e:
                    print(f"[错误] 上传失败,尝试重新初始化客户端：{file_path} → {e}")
                    client = init_115_client()

                #print(f"[信息] 单个文件处理完成，休眠 {SLEEP_AFTER_FILE} 秒...")
                time.sleep(SLEEP_AFTER_FILE)

        print(f"[信息] 一轮遍历完成，休眠 {SLEEP_AFTER_ROUND} 秒...")
        time.sleep(SLEEP_AFTER_ROUND)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[信息] 用户终止程序")
    except Exception as e:
        print(f"[错误] 程序异常：{e}")