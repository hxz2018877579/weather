import requests
import json
import time
import re
from datetime import datetime, timedelta, timezone

# 请在这里填入你的百度智能云API Key和Secret Key
# 这两个密钥是调用百度智能云文本纠错服务所必需的
# 你可以在百度智能云控制台的应用管理中找到
API_KEY = "t5QKpiTvTIlTBfCR6Xhsv1rE"
SECRET_KEY = "HUgutdUDkNYC3GwwBFQJa6VVNieMVXVD"
TIME_VALIDATION_ENABLED = True  # 是否启用时间验证
MAX_TIME_AHEAD_HOURS = 2       # 最大允许提前的小时数
ADJUST_PAST_TIME = False       # 是否调整过去的时间
# 全局变量用于存储access_token和过期时间，避免频繁获取
ACCESS_TOKEN = None
TOKEN_EXPIRES_AT = 0

def get_access_token():
    """
    使用API Key和Secret Key获取百度智能云的access_token。
    token有效期为30天，本函数会缓存token，在过期前无需再次请求。
    """
    global ACCESS_TOKEN, TOKEN_EXPIRES_AT
    
    # 检查当前token是否有效且未过期
    if ACCESS_TOKEN and time.time() < TOKEN_EXPIRES_AT:
        return ACCESS_TOKEN

    url = f"https://aip.baidubce.com/oauth/2.0/token?grant_type=client_credentials&client_id={API_KEY}&client_secret={SECRET_KEY}"
    try:
        response = requests.post(url)
        response.raise_for_status()
        result = response.json()
        ACCESS_TOKEN = result.get("access_token")
        expires_in = result.get("expires_in", 0)
        # 提前5分钟过期以防万一
        TOKEN_EXPIRES_AT = time.time() + expires_in - 300 
        print("成功获取新的百度智能云Access Token。")
        return ACCESS_TOKEN
    except requests.exceptions.RequestException as e:
        print(f"获取access_token失败: {e}")
        return None

def correct_text(input_text, retries=3):
    """
    使用百度智能云文本纠错服务进行内容纠错，并增加重试机制。
    
    参数:
    input_text (str): 待纠错的文本内容。
    retries (int): 重试次数。
    
    返回:
    str: 纠错后的文本内容。如果API调用失败，则返回原始文本。
    """
    if API_KEY == "YOUR_BAIDU_API_KEY" or SECRET_KEY == "YOUR_BAIDU_SECRET_KEY":
        return f"错误：请在 corrector.py 文件中设置您的API_KEY和SECRET_KEY，目前返回原始文本：\n{input_text}"

    for attempt in range(retries):
        access_token = get_access_token()
        if not access_token:
            return f"错误：无法获取API访问令牌，请检查网络连接或API密钥，目前返回原始文本：\n{input_text}"

        url = f"https://aip.baidubce.com/rpc/2.0/nlp/v1/ecnet?access_token={access_token}"
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        payload = json.dumps({"text": input_text})
        
        try:
            response = requests.post(url, data=payload, headers=headers, timeout=10)
            response.raise_for_status()
            result = response.json()
            
            # 百度智能云的文本纠错API返回格式
            if result.get("item"):
                corrected_content = result["item"].get("correct_query")
                if corrected_content:
                    # >>> 调用修正后的时间验证和调整函数 <<<
                    adjusted_content = validate_and_adjust_time(corrected_content)
                    return f"[纠错成功]\n{adjusted_content}"
                else:
                    return "没有发现需要纠错的内容。"
            else:
                return f"API返回错误信息: {result.get('error_msg', '未知错误')}"
        except requests.exceptions.Timeout:
            print(f"API请求超时，尝试重试 ({attempt + 1}/{retries})...")
            time.sleep(2)
            continue
        except requests.exceptions.RequestException as e:
            return f"API请求失败: {e}，目前返回原始文本：\n{input_text}"
        except Exception as e:
            return f"处理响应时出错: {e}，目前返回原始文本：\n{input_text}"
    
    return f"多次重试后仍然失败，请稍后重试，目前返回原始文本：\n{input_text}"


def validate_and_adjust_time(text):
    """
    验证文本中的时间是否合理，并自动调整不合理的时间。
    
    **更新：现在检查时间是否在当前北京时间 ±MAX_TIME_AHEAD_HOURS (2小时) 的范围内。**
    
    参数:
    text (str): 待验证的文本内容
    
    返回:
    str: 调整后的文本内容
    """
    global ADJUST_PAST_TIME 
    
    if not TIME_VALIDATION_ENABLED:
        return text

    # >>> 获取北京时间 (UTC+8) <<<
    # 获取当前 UTC 时间
    utc_now = datetime.now(timezone.utc)
    # 北京时间是 UTC+8
    now = utc_now + timedelta(hours=8)
    # 将 now 对象转换为无时区信息，以便与 text_time (无时区) 进行正确比较
    now = now.replace(tzinfo=None)

    # >>> 计算允许的时间范围边界 <<<
    # 最小允许时间：北京时间 - 2小时
    min_allowed_time = now - timedelta(hours=MAX_TIME_AHEAD_HOURS)
    # 最大允许时间：北京时间 + 2小时
    max_allowed_time = now + timedelta(hours=MAX_TIME_AHEAD_HOURS)
    
    # 在文本中查找时间模式 (X时X分)
    time_pattern = r'(\d{1,2})时(\d{1,2})分'
    
    # 定义替换函数，re.sub 会对每一个匹配项调用此函数
    def time_replacer(match):
        """为每一个匹配到的时间字符串执行验证和调整逻辑。"""
        try:
            hour = int(match.group(1))
            minute = int(match.group(2))
            
            # 创建时间对象 (假设日期为 now 的日期)
            text_time = datetime(now.year, now.month, now.day, hour, minute)
        
        except ValueError:
            # 时间值无效 (如小时>23或分钟>59)，不进行替换
            return match.group(0) 

        target_time_to_adjust_to = None
        log_message = None

        # 1. 检查时间是否超过最大允许时间 (太超前)
        if text_time > max_allowed_time:
            # 调整到最大允许时间边界 (now + 2小时)
            target_time_to_adjust_to = max_allowed_time
            log_message = "超前"
        
        # 2. 检查时间是否小于最小允许时间 (太滞后/太早)
        elif text_time < min_allowed_time:
            # 调整到最小允许时间边界 (now - 2小时)
            target_time_to_adjust_to = min_allowed_time
            log_message = "滞后"

        
        if target_time_to_adjust_to:
            adjusted_hour = target_time_to_adjust_to.hour
            # 使用 :02d 格式化分钟，确保是两位数 (例如 5 -> 05)
            adjusted_minute = target_time_to_adjust_to.minute
            
            new_time_str = f"{adjusted_hour}时{adjusted_minute:02d}分"
            print(f"时间已调整 ({log_message}，超出 ±{MAX_TIME_AHEAD_HOURS}小时范围): {match.group(0)} -> {new_time_str}")
            return new_time_str


        # 如果时间在 [min_allowed_time, max_allowed_time] 范围内，则返回原始匹配的字符串
        return match.group(0)

    # 使用 re.sub 并传入回调函数 time_replacer 进行替换
    adjusted_text = re.sub(time_pattern, time_replacer, text)
    
    return adjusted_text
