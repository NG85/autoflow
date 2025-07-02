import requests
import json

# 配置你的飞书应用信息
APP_ID = 'cli_a74bce3ec73d901c'
APP_SECRET = '1xC7zUP6PQpUoOMJte8tddgPm5zaqfoW'

users = [
    {
        "name": "黄漫绅",
        "email": "huangmanshen@pingcap.cn",
        "open_id": "ou_94c989b8380feee4e9d03a4513cfcddb"
    },
    {
        "name": "赵梦梦",
        "email": "mengmeng.zhao@pingcap.cn",
        "open_id": "ou_46250e2e81dc91f585d559375d11e7f7"
    },
    {
        "name": "彭琴",
        "email": "pengqin@pingcap.cn",
        "open_id": "ou_2e86426185c9a7d78f720cef5f4911ca"
    },
    {
        "name": "林微",
        "email": "wei.lin@pingcap.cn",
        "open_id": "ou_edbdc2e3fc8eb411bbc49cc586629709"
    },
    {
        "name": "Louis",
        "email": "ls@pingcap.cn",
        "open_id": "ou_adcaafc471d57fc6f9b209c05c0f5ce1"
    },
    {
        "name": "崔秋",
        "email": "cuiqiu@pingcap.cn",
        "open_id": "ou_718d03819e549537c4dc972154798a81"
    },
    {
        "name": "于旸",
        "email": "yuyang@pingcap.cn",
        "open_id": "ou_50302b6787a738a51863a2a06e2943b9"
    },
    {
        "name": "郑海聪",
        "email": "haicong.zheng@pingcap.cn",
        "open_id": "ou_7453426513fd5882c93e3d784ba512a3"
    },
    {
        "name": "柴多",
        "email": "duo.chai@pingcap.cn",
        "open_id": "ou_84db97160136ce77ad821c1c4ef02d0f"
    },
    {
        "name": "周国平",
        "email": "guoping.zhou@pingcap.cn",
        "open_id": "ou_131721439965012cc0b5d375d1189eba"
    },
    {
        "name": "韩启微",
        "email": "qiwei.han@pingcap.cn",
        "open_id": "ou_1c90c4689c5b482d478fb9193d6dcaff"
    },
    {
        "name": "崔佳宁",
        "email": "cuijianing@pingcap.cn",
        "open_id": "ou_20effbbb4cb09e83d3f55d964bb40813"
    },
    {
        "name": "姜佳莲",
        "email": "",
        "open_id": "ou_130ba876801491a7967d243caed502c3"
    },
    {
        "name": "姚亮",
        "email": "",
        "open_id": "ou_47fc28d13dd04718bbe65a3c0b010d29"
    },
    {
        "name": "王静",
        "email": "wangjing@pingcap.cn",
        "open_id": "ou_81012fce4e252eceb15183cd7f821f81"
    },
    {
        "name": "黄昊",
        "email": "hao.huang@pingcap.cn",
        "open_id": "ou_4c8a5c9e41071423f82fce73d326120f"
    },
    {
        "name": "易鹏",
        "email": "peng.yi@pingcap.cn",
        "open_id": "ou_d1caf831306f5f62918374351ad74549"
    },
    {
        "name": "肖华",
        "email": "xiaohua@pingcap.cn",
        "open_id": "ou_36abbce391261ebf4f8c22c8a237d3ef"
    },
    {
        "name": "刘晓绵",
        "email": "",
        "open_id": "ou_f5a198418832320f60d9c731e91290d3"
    },
    {
        "name": "金豫玮",
        "email": "",
        "open_id": "ou_c53c2f904af4531eff0dba69a76c44a1"
    },
    {
        "name": "朱舜杰",
        "email": "",
        "open_id": "ou_8c9325b78d7aef43006a7ada68fae658"
    },
    {
        "name": "殷小静",
        "email": "xiaojing.yin@pingcap.cn",
        "open_id": "ou_93f289c978c75cb1dbe592e8014fd6ba"
    },
    {
        "name": "钟艳珍",
        "email": "yanzhen.zhong@pingcap.cn",
        "open_id": "ou_0b26ba63639e89dde8d40d154c2b44b8"
    },
    {
        "name": "余梦杰",
        "email": "jason.yu@pingcap.cn",
        "open_id": "ou_f750a3628dc15388a198c643ce786910"
    },
]

group_chats = []

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
    # content = {
    #     "text": text
    # }
    payload = {
        "receive_id": receive_id,
        "msg_type": "post",
        # "content": json.dumps(content, ensure_ascii=False)
        "content": json.dumps(text, ensure_ascii=False)
    }
    print(payload)
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
    
    # 要发送的内容和链接
    url = "https://mi5p6bgsnf8.feishu.cn/docx/RBtzdqXQuoQdjLxtCpIcmmZEnuc"
    text = {
        "zh_cn": {
            "title": "",
            "content": [
                [
                    {
                        "tag": "md",
                        "text": (
                            f"**APTSell（网页端）上线新功能啦，快来试试吧～**\n\n"
                            f"---\n"
                            f"**网页端功能：**\n"
                            f"1. 查询模式新增【商务运营助手】（商务运营助手模式已接入商务运营部门的wiki知识库全部内容，后续可以在这个模式下询问任何有关于商务运营除视频以外的所有内容。）\n"
                            f"    因此查询模式一共有以下4类：\n"
                            f"    - knowledge（产品方案等企业专有销售打单知识）\n"
                            f"    - Biz operation（商务运营部门wiki知识库）\n"
                            f"    - crm（区分权限的crm客户项目订单信息）\n"
                            f"    - mixed（含以上3类问答模式内所有内容）\n"
                            f"2. crm信息的精准查询与引用（支持条件筛选方式精准查询crm信息，并将信息精准引用到对话中。）\n\n"
                            f"\n\n"
                            f"**飞书机器人功能：**\n"
                            f"1. 查询模式新增【商务运营助手】（接入内容与网页端一致）因此查询模式一共有以下4类：\n"
                            f"    - 销售大脑\n"
                            f"    - 商务运营助手\n"
                            f"    - CRM助手\n"
                            f"    - 综合问答\n\n"
                            f"---\n"
                            f"具体功能的详细说明和操作指南参考文档：[【平凯】销售智能助手—APTSell功能上线说明]({url})"
                        )
                    }
                ]
            ]
        }
    }
    
    # 发送release notes
    for user in users:
        receive_id = user.get("open_id")
        receive_id_type = "open_id"
        if not receive_id:
            if user.get("email"):
                receive_id = user["email"]
                receive_id_type = "email"
            else:
                print(f"用户 {user['name']} 没有可用的open_id/email，无法发送")
                continue
        resp = send_message_to_user(receive_id, token, text, receive_id_type)
        print(f"发送给 {user['name']} Release Notes 结果: {resp}")
    
    for group_chat in group_chats:
        resp = send_message_to_user(group_chat["chat_id"], token, text, "chat_id")
        print(f"发送给 {group_chat['name']} Release Notes 结果: {resp}")