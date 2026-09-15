"""agent 插件的配置校验。

这一条单独测，是因为它守的不是 agent 的某种行为，而是一个**给 CLI 看的契约**：

> 插件在配置写错时必须抛 `DuGentXError` 的子类。

`dugentx/cli.py` 只接这一种异常。抛别的类型（比如 `ValueError`）的后果是：
用户拼错一个键名，得到的是一段栈回溯加退出码 1，而不是那一行

```
dugentx: agent 配置里有不认识的键：['max_step']；可用：[...]
```

拼错键名是**最容易犯也最该被好好告知**的一类错误，所以它值得一条测试盯着。
"""

from __future__ import annotations

import pytest

from dugentx.kernel.errors import DuGentXError
from dugentx.plugins.agent import config_from


def test_a_typo_in_the_agent_config_is_something_the_cli_can_report() -> None:
    """未知键必须抛 DuGentXError，而不是 ValueError。"""
    with pytest.raises(DuGentXError) as info:
        config_from({"max_step": 3})  # 少了一个 s

    # 报错要指明两件事：错的是哪个键，以及能用的是哪些。
    assert "max_step" in str(info.value)
    assert "max_steps" in str(info.value)
