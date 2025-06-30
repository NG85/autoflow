import requests
import json

# 配置你的飞书应用信息
APP_ID = 'cli_a74bce3ec73d901c'
APP_SECRET = '1xC7zUP6PQpUoOMJte8tddgPm5zaqfoW'
HOST = "https://aptsell.pingcap.net"
MESSAGE_0620_EXECUTION_ID = "cuyMRzDZslxhWAXbLmRrQKY"
MESSAGE_0605_EXECUTION_ID = "ccxBqousiagbzqvhSESxBkY"

# 用户信息
admins = [
    {
        "name": "崔秋",
        "email": "cuiqiu@pingcap.cn",
        "open_id": "ou_718d03819e549537c4dc972154798a81"
    },
    {
        "name": "余梦杰",
        "email": "jason.yu@pingcap.cn", 
        "open_id": ""
    },
    {
        "name": "龙恒",
        "email": "ls@pingcap.cn",
        "open_id": "ou_adcaafc471d57fc6f9b209c05c0f5ce1"
    },
    {
        "name": "林微",
        "email": "wei.lin@pingcap.cn",
        "open_id": "ou_edbdc2e3fc8eb411bbc49cc586629709"
    }
]

leaders = [
  {
    "name": "韩启微",
    "email": "qiwei.han@pingcap.cn",
    "open_id": "ou_1c90c4689c5b482d478fb9193d6dcaff"
  }
]

sales = [
    {
        "name": "钟艳珍",
        "email": "yanzhen.zhong@pingcap.cn",
        "open_id": "ou_0b26ba63639e89dde8d40d154c2b44b8",
        "accounts": [
            {"execution_id": "bzgrIeUMFzNJhlUvSACMnOs", "account_id": "669e127f8adb9b0001760b54", "account_name": "厦门亿联网络技术股份有限公司"},
            {"execution_id": "RNaQJEnJOKjCxjiioipast", "account_id": "680a0707d515490001988739", "account_name": "四川达威科技股份有限公司"},
            {"execution_id": "ZldbMCPjHaesvNeKUmicwM", "account_id": "632937ea8dffb400013e797c", "account_name": "安捷利美维电子（厦门）有限责任公司"},
            {"execution_id": "bDDtbbGoAbeDGPilOyhQTPq", "account_id": "682b49058472640001bcd4bd", "account_name": "广州爱浦路网络技术有限公司"},
            {"execution_id": "dMjncAQPhWfEGmMnRYNgOhJ", "account_id": "6209d2a26f60d70001472975", "account_name": "深圳TCL数字技术有限公司"},
            {"execution_id": "bEoxXEdeUnkqEPGXPaAyRip", "account_id": "67b48f24b7496c0001dd6410", "account_name": "深圳奥尼电子股份有限公司"},
            {"execution_id": "ewxWPLUvlttKXVTRCGCXeQN", "account_id": "67e66b662cc8780001eea697", "account_name": "深圳市佳创视讯技术股份有限公司"},
            {"execution_id": "fuOfyNxAymJDgDcPIcNSLKn", "account_id": "683cdb5e94f4110001779e17", "account_name": "深圳市安冠科技有限公司"},
            {"execution_id": "UGwxeOLECZlHttvyXwFAeg", "account_id": "67e9586490aa2600019fa838", "account_name": "深圳航空有限责任公司"},
            {"execution_id": "fvIdwHXRzXqNyrNcoAWhKsS", "account_id": "68305c2720d3d1000102fe26", "account_name": "深圳谷探科技有限公司"},
            {"execution_id": "dWzNFjkkOlAAmnxSwKxoiNa", "account_id": "67e18ec590aa260001ae33fa", "account_name": "赛晶亚太半导体科技（浙江）有限公司"},
        ]
    },
    {
        "name": "金豫玮",
        "email": "yuwei.jin@pingcap.cn",
        "open_id": "ou_c53c2f904af4531eff0dba69a76c44a1",
        "accounts": [
            {"execution_id": "SYlbGKARABBcZwYBZopOeb", "account_id": "684a7700cb8d9100017d4a72", "account_name": "上海应谱科技有限公司"},
            {"execution_id": "fYeBtZHotnkeetITclFWlsI", "account_id": "67e1439c784f84000166100e", "account_name": "上海爱数信息技术股份有限公司"},
            {"execution_id": "dRonbHrFaYdyCKvxthEIaqh", "account_id": "6819ad9838075c0001f55c44", "account_name": "上海电气风电集团股份有限公司"},
            {"execution_id": "cUCptBKvtzIPBTIVBPPictM", "account_id": "67fb9aa71bc35400018eadbd", "account_name": "上海达美乐比萨有限公司"},
            {"execution_id": "boRepTdxOdLSXepwJAMuBZY", "account_id": "680905f26c12600001ff9834", "account_name": "中科计算技术西部研究院"},
            {"execution_id": "cjrDOaRvPyJhpFmWNSRZyxv", "account_id": "683885896da03f0001f3fe45", "account_name": "中远海运科技股份有限公司"},
            {"execution_id": "dnqnjmmUMpXBBTWktSGfPGM", "account_id": "6811854238075c0001a0a893", "account_name": "北京麦聪软件有限公司"},
            {"execution_id": "batdKkkKYQuzimjVcXwgCZd", "account_id": "67fb7c9368ef650001f56feb", "account_name": "南京北路智控科技股份有限公司"},
            {"execution_id": "feogCwphHWJsxHyOMSYuBOo", "account_id": "684690f84d967b00016ea5d6", "account_name": "唐山百川智能机器股份有限公司"},
            {"execution_id": "czMvPSeVkxDUHZIyCfJIZaE", "account_id": "68332aa4e2e3fc00016d12bd", "account_name": "嘉必优生物技术（武汉）股份有限公司"},
            {"execution_id": "bgAmbuXhdZiGvqmIdNzsExB", "account_id": "634f4e07e0670f0001bb290f", "account_name": "国轩高科股份有限公司"},
            {"execution_id": "bTOjhhELJqywhHssEMqVBxI", "account_id": "68090131d5154900011d5fcd", "account_name": "新开普电子股份有限公司"},
            {"execution_id": "dXuljgaauilRkzEddVpfKBl", "account_id": "681077e4a9d9f5000167c305", "account_name": "河北雄安首亨科技有限公司"},
            {"execution_id": "eoIriaUmXsPnDbMHHFuvJxF", "account_id": "6808bc23d6eb5e0001792d67", "account_name": "浙江天怀数智科技有限公司"},
            {"execution_id": "fLvAxwQVDQhqLeIyZjKRuHj", "account_id": "67fa0f7f51b3c10001deb5b2", "account_name": "湖北三宁化工股份有限公司"},
            {"execution_id": "bzHgwwxMOgTaKvPvfFBZAgl", "account_id": "67ff5e86dc7ef200018a3123", "account_name": "湖北东贝机电集团股份有限公司"},
            {"execution_id": "eYysBDgkEARuSZoOJyOzEAa", "account_id": "68332b1949fc0600014f1656", "account_name": "湖北文化旅游集团有限公司"},
            {"execution_id": "cbPUanvPccJRyRXhXAfUisr", "account_id": "67d2e715641f130001ab3343", "account_name": "福建星网锐捷通讯股份有限公司"},
            {"execution_id": "tsOmRBmVefZMKNZtGuMaEu", "account_id": "67e15bc47463260001d7f56a", "account_name": "苏州科达科技股份有限公司"},
        ]
    },
    {
        "name": "韩启微",
        "email": "qiwei.han@pingcap.cn",
        "open_id": "ou_1c90c4689c5b482d478fb9193d6dcaff",
        "accounts": [
            {"execution_id": "cvAObZORblVADUXDazaudxh", "account_id": "68351ca249fc06000196d390", "account_name": "金华春光橡塑科技股份有限公司"},
            {"execution_id": "fQowgcxEcarlnaMXadNCyRJ", "account_id": "6839978b49fc0600012d3345", "account_name": "江苏财经职业技术学院"},
            {"execution_id": "fxOswSTHCoOPDFjqZboCkWf", "account_id": "6757d4dbb860bb00017cc003", "account_name": "北京治真治合科技有限公司"}
        ]
    # },
    # {
    #     "name": "姚亮",
    #     "email": "liang.yao@pingcap.cn",
    #     "open_id": "ou_47fc28d13dd04718bbe65a3c0b010d29",
    #     "accounts": []
    }
]

def get_tenant_access_token(app_id, app_secret):
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/"
    resp = requests.post(url, json={
        "app_id": app_id,
        "app_secret": app_secret
    })
    resp.raise_for_status()
    return resp.json()["tenant_access_token"]

def get_open_id_by_email(email, token):
    url = f"https://open.feishu.cn/open-apis/contact/v3/users/batch_get_id"
    headers = {"Authorization": f"Bearer {token}"}
    if email:
        resp = requests.post(url, json={"emails": [email]}, headers=headers)
    else:
        return None
    resp.raise_for_status()
    data = resp.json()
    return data["data"]["user_list"][0]["user_id"] if data["data"]["user_list"] else None

def send_message_to_user(receive_id, token, text, receive_id_type="open_id"):
    api_url = "https://open.feishu.cn/open-apis/im/v1/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    # 文本消息内容
    content = {
        "text": text
    }
    payload = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps(content)
    }
    params = {"receive_id_type": receive_id_type}
    resp = requests.post(api_url, params=params, headers=headers, data=json.dumps(payload))
    if resp.status_code != 200:
        return resp.text
    resp.raise_for_status()
    return resp.json()

if __name__ == "__main__":
    # 1. 获取token
    token = get_tenant_access_token(APP_ID, APP_SECRET)
    print(token)
    # 2. 发送模版1-业绩变化review表给admins
    for user in admins:
        receive_id = user.get("open_id")
        receive_id_type = "open_id"
        if not receive_id:
            if user.get("email"):
                receive_id = user["email"]
                receive_id_type = "email"
            else:
                print(f"用户 {user['name']} 没有可用的open_id/email，无法发送")
                continue
        # 要发送的内容和链接
        start_date_1 = "2025-06-20"
        end_date_1 = "2025-06-27"
        url_1 = f"{HOST}/review/weeklyDetail/{MESSAGE_0620_EXECUTION_ID}?reportName=%E4%B8%9A%E7%BB%A9%E5%8F%8A%E5%8F%98%E5%8C%96%E7%BB%9F%E8%AE%A1%E8%A1%A8-%5B{start_date_1}%20-%20{end_date_1}%5D"
        start_date_2 = "2025-06-05"
        end_date_2 = "2025-06-27"
        url_2 = f"{HOST}/review/weeklyDetail/{MESSAGE_0605_EXECUTION_ID}?reportName=%E4%B8%9A%E7%BB%A9%E5%8F%8A%E5%8F%98%E5%8C%96%E7%BB%9F%E8%AE%A1%E8%A1%A8-%5B{start_date_2}%20-%20{end_date_2}%5D"
        text = (
            f"Sia帮您生成了[【{start_date_1}~{end_date_1}的业绩变化review表】]({url_1})，请点击链接查看详情。\n"
            f"Sia帮您生成了[【{start_date_2}~{end_date_2}的业绩变化review表】]({url_2})，请点击链接查看详情。\n"
        )
        resp = send_message_to_user(receive_id, token, text, receive_id_type)
        print(f"发送给 {user['name']}业绩变化review表 结果: {resp}")
    
    # 3. 发送模版2-团队的account review表给sales
    for user in sales:
        receive_id = user.get("open_id")
        receive_id_type = "open_id"
        if not receive_id:
            if user.get("email"):
                receive_id = user["email"]
                receive_id_type = "email"
            else:
                print(f"用户 {user['name']} 没有可用的open_id/email，无法发送")
                continue
        time_range = "2025-06-20~2025-06-27"
        # 组装每个account的review链接，格式为 [客户名称](url)
        account_lines = [
            f"- [{acc['account_name']}]({HOST}/review/detail/{acc['execution_id']}?accountId={acc['account_id']})"
            for acc in user.get("accounts", [])
        ]
        accounts_text = "\n".join(account_lines)
        text = (
            f"Sia帮您生成了【account review（{time_range}）】，请查收：\n"
            f"{accounts_text}"
        )
        resp = send_message_to_user(receive_id, token, text, receive_id_type)
        print(f"发送给 {user['name']}的account review表 结果: {resp}") 

    # 4. 汇总所有sales的account review给leader
    leader_report_lines = []
    for user in sales:
        if not user.get("accounts"):
            continue
        leader_report_lines.append(f"{user['name']}:")
        for acc in user["accounts"]:
            url = f"{HOST}/review/detail/{acc['execution_id']}?accountId={acc['account_id']}"
            leader_report_lines.append(f"- [{acc['account_name']}]({url})")
        leader_report_lines.append("")  # 空行分隔

    leader_text = (
        f"Sia帮您生成了【account review（{time_range}）】，请查收：\n"
        + "\n".join(leader_report_lines)
    )

    for leader in leaders:
        receive_id = leader.get("open_id")
        receive_id_type = "open_id"
        if not receive_id:
            if leader.get("email"):
                receive_id = leader["email"]
                receive_id_type = "email"
            else:
                print(f"Leader {leader['name']} 没有可用的open_id/email，无法发送")
                continue
        resp = send_message_to_user(receive_id, token, leader_text, receive_id_type)
        print(f"发送给 {leader['name']}的account review总表 结果: {resp}") 