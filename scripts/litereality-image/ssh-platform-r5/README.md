# r5 直接 sshd 启动兼容修复

学校 r4 实测：sshd -t 报 no hostkeys available；执行 service ssh start 生成密钥后，ssh root@127.0.0.1 登录成功。这确认了缺少主机密钥，尚不能确定平台具体启动命令或外部端口映射。

参考镜像直接执行 /usr/sbin/sshd 可运行；r4 直接执行时失败。r5 补齐这条路径：/usr/sbin/sshd 作为启动前初始化入口，真实 OpenSSH 二进制通过 dpkg-divert 保存在 /usr/lib/openssh/sshd.room-real。运行服务器时生成本容器的独立密钥，配置测试/版本查询直接转交真实程序。修改系统服务的进程匹配路径，确保 stop/start/restart 正常。未烘焙任何共享主机私钥或密码。

以已加载的 r4 为基础，在 x86_64 Docker 主机解压源码包后：

```bash
cd room-image-build
bash scripts/ssh-platform-r5/build_and_test.sh
```

脚本构建 r5、执行 28 项 SSH 验收与 CPU 验收，再导出标准 Docker tar 到 return_output。校验和在同名 .sha256 文件中。成品 .docker.tar.gz 由已验证的 tar 压缩而成，学校直接导入成品即可，无需运行构建脚本。

本次已测试：保留 r4 的 22 项检查，以及原始 sshd 前台启动（包含临时空 /run）、密码注入、后台 sshd 加保活、系统服务停止/启动/重启、主机密钥保持、配置检查无初始化递归。所有登录测试是真实 OpenSSH 客户端连接。

学校实际使用 r5 的结果仍待确认；当前 r4 已手动恢复本地 SSH，不必为查看当前结果立即重建容器。三天到期后重建才会再次失去容器自身生成的密钥，因此后续新容器优先使用 r5。
