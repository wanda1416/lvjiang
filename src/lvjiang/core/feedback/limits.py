"""反馈通道的数值上限（客户端与服务端的唯一对照处）。

这些数字都不是随手定的：

* ``MAX_ATTACHMENT_BYTES`` 对齐服务端 ``feedback_attachment.payload`` 的单值上限
  （900KB，D1 单行 1MB 硬线之下），超了必然 413；
* ``MAX_SCREENSHOT_BYTES`` 是**客户端自己**的目标——PNG 已经压过，再过 gzip 基
  本不涨，留 200KB 余量保证每个附件都能落到服务端上限以内；
* ``MAX_SCREENSHOTS`` / ``MAX_ATTACHMENTS`` 与 ``server/src/feedback.mjs`` 的
  ``KIND_LIMITS`` 一致（截图 10、日志 1、体检报告 1、总数 12）。

改任何一个数字都要同步改服务端，否则用户会在最后一张图上看到 409/413。
"""

#: 截图张数上限（与产品口径一致：10 张）。
MAX_SCREENSHOTS = 10

#: 附件总数上限：10 截图 + 1 日志 + 1 体检报告。
MAX_ATTACHMENTS = 12

#: 单张截图压缩后的目标字节数。
MAX_SCREENSHOT_BYTES = 700_000

#: 单个附件 gzip 后的硬上限（服务端同值）。
MAX_ATTACHMENT_BYTES = 900_000

#: 截图最长边（超出按比例缩小；4K 截图缩到 1920 仍能看清界面文字）。
SCREENSHOT_MAX_EDGE = 1920

#: 日志片段保留的最大行数（尾部）。够覆盖一次任务，又不至于把日志整包拖走。
LOG_TAIL_LINES = 2000

#: 从日志文件尾部最多读取的原始字节（截断后仍按行收口）。
LOG_TAIL_MAX_BYTES = 1_500_000

#: 可选日志的天数窗口（与 logs 的 7 天保留期一致）。
LOG_DAY_WINDOW = 7
