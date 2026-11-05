# r4 SSH 平台兼容修复

针对 r3 实测发现的两类启动故障：重建目录初始化冲突会阻止 SSH 启动；平台覆盖 ENTRYPOINT 后调用 service ssh start，会因无主机密钥而失败。参考镜像为 sdmap-metric3d:20260709_cuda121_torch231_metric3dv2_ssh_np126_tools。

r4 不在 SSH 启动前初始化重建项目。默认入口使用 /usr/local/bin/start-ssh.sh；保留 room-sshd 别名。使用 /etc/ssh/sshd_config，允许平台提供的公钥或非空密码，支持 ROOT_PASSWORD 优先、SSH_PASSWORD 次之。不预置登录密码，不复制参考镜像的主机私钥；在启动服务时生成每个容器自己的密钥。

平台保留 ENTRYPOINT、只覆盖 CMD 为保活命令时，入口会先启动 SSH。平台覆盖 ENTRYPOINT、调用 service ssh start/restart 时，系统服务也会初始化密钥。环境变量 ROOM_SSH_AUTOSTART=0 只关闭入口为普通命令启动的后台 SSH。ROOM_SSH_PORT 可指定容器监听端口；学校映射端口由平台决定。

使用成品 r4 镜像时无需运行构建脚本。若需要从 r3 复建，在已有 Docker 和 r3 镜像的 x86_64 Linux 上，将包解压到工作目录后执行：

```bash
cd room-image-build
bash scripts/ssh-platform-r4/build_and_test.sh
```

构建不下载依赖。验收覆盖默认/覆盖入口、冲突/只读项目、公钥和真实密码登录、错误凭据拒绝、重启、SFTP、Ghostty/tmux、CPU 应用环境。输出统一在 return_output，测试密钥和临时容器会清理。

测试与修复仅代表已复现路径通过；学校实际入口和日志不可见，最终需用户上传后确认。平台如果同时覆盖 ENTRYPOINT 和 CMD，直接调用原始 /usr/sbin/sshd，须由平台先生成主机密钥，或改用 /usr/local/bin/start-ssh.sh 或 service ssh start。本修复不会把共用主机私钥烘焙进镜像。

Codex 容器执行模式、GPU 内核及真实房间流程未在本次修改；此前 room-check 的内层沙箱检查仍可能失败。
