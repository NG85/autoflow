import json
import logging
from typing import Optional
import redis
from app.core.config import settings

logger = logging.getLogger(__name__)


class RedisClient:
    """Redis客户端工具类"""
    
    def __init__(self):
        # 从CELERY_BROKER_URL解析Redis连接信息
        # 格式: redis://redis:6379/0
        redis_url = settings.CELERY_BROKER_URL
        if redis_url.startswith('redis://'):
            # 移除 redis:// 前缀
            redis_url = redis_url[8:]
            # 分离主机和数据库
            if '/' in redis_url:
                host_port, db = redis_url.rsplit('/', 1)
                db = int(db)
            else:
                host_port = redis_url
                db = 0
            
            # 分离主机和端口
            if ':' in host_port:
                host, port = host_port.split(':', 1)
                port = int(port)
            else:
                host = host_port
                port = 6379
                
            self.redis_client = redis.Redis(
                host=host,
                port=port,
                db=db,
                decode_responses=True
            )
        else:
            # 默认配置
            self.redis_client = redis.Redis(
                host='redis',
                port=6379,
                db=0,
                decode_responses=True
            )
    
    def _set_access_token(self, platform: str, user_id: str, access_token: str, type: str, expires_in: int = 7200) -> bool:
        """
        通用方法：存储平台access token
        
        Args:
            platform: 平台名称 (feishu/lark)
            user_id: 用户ID
            access_token: 访问令牌
            type: 令牌类型
            expires_in: 过期时间（秒），默认2小时
            
        Returns:
            是否存储成功
        """
        try:
            key = f"token:{platform}:user:{user_id}:{type}"
            # 按照API返回格式存储
            token_data = {
                "code": 0,
                "access_token": access_token,
                "expires_in": expires_in
            }
            # 设置过期时间比token过期时间短一些，确保安全
            self.redis_client.setex(key, expires_in - 200, json.dumps(token_data))
            logger.info(f"Successfully stored {platform} access token for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to store {platform} access token for user {user_id}: {e}")
            return False
    
    def _get_access_token(self, platform: str, user_id: str, type: str) -> Optional[str]:
        """
        通用方法：获取平台access token
        
        Args:
            platform: 平台名称 (feishu/lark)
            user_id: 用户ID
            type: 令牌类型
            
        Returns:
            access token，如果不存在或已过期则返回None
        """
        try:
            key = f"token:{platform}:user:{user_id}:{type}"
            token_data = self.redis_client.get(key)
            if token_data:
                try:
                    data = json.loads(token_data)
                    access_token = data.get("access_token")
                    if access_token:
                        logger.info(f"Successfully retrieved {platform} access token for user {user_id}")
                        return access_token
                    else:
                        logger.warning(f"No access_token in stored data for user {user_id}")
                        return None
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse token data for user {user_id}: {e}")
                    return None
            else:
                logger.info(f"No {platform} access token found for user {user_id}")
                return None
        except Exception as e:
            logger.error(f"Failed to get {platform} access token for user {user_id}: {e}")
            return None

# 全局Redis客户端实例
redis_client = RedisClient() 