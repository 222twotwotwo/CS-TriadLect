// 数据热重载：盯着 content/ 下那几个文件，内容一变就重新解析、重新应用到世界上。
//
// 为什么是「逐字节比对」而不是「比 mtime」：mtime 在多数文件系统上只有 1 秒分辨率，
// 而学生改参数的手速恰恰是"改一下、切回来看一眼、再改一下"—— 两次存盘落在同一秒里，
// 第二次就漏了，然后他会说"这破热重载时好时坏"。逐字节比对 2KB 文本是零成本，
// 顺带白拿一个好处：存了盘但内容没变，不会误触发一次重载。
#pragma once

#include <string>
#include <vector>

#include "content.h"
#include "world.h"

namespace dlab {

class ContentWatcher {
public:
    explicit ContentWatcher(std::vector<std::string> files) : files_(std::move(files)) {
        lastBytes_.resize(files_.size());
    }

    // 启动时立刻读一遍并应用：让「文件里写的东西」直接成为世界的初始状态，
    // 而不是让代码里的默认值和文件里的值各说各话。
    int prime(World& world, std::vector<std::string>* log = nullptr) {
        for (size_t i = 0; i < files_.size(); ++i) readTextFile(files_[i], lastBytes_[i]);
        return applyContentFiles(world, files_, log);
    }

    // 重新读盘比对；有变化的文件会被重新应用。返回发生变化的文件路径。
    std::vector<std::string> poll(World& world, std::vector<std::string>* log = nullptr) {
        ++polls_;
        std::vector<std::string> changed;
        for (size_t i = 0; i < files_.size(); ++i) {
            std::string bytes;
            readTextFile(files_[i], bytes);
            if (bytes == lastBytes_[i]) continue;
            lastBytes_[i] = bytes;  // 先记下来：解析失败也不能每个轮询周期重复报一遍
            changed.push_back(files_[i]);
            if (bytes.empty()) continue;  // 文件被删了：上面的 changed 已经能说明问题
            applyContentFiles(world, {files_[i]}, log);
            ++reloads_;
        }
        return changed;
    }

    // 按 R / 敲 reload 命令时用：假装所有文件都变了，强制重读一遍
    std::vector<std::string> forceReload(World& world, std::vector<std::string>* log = nullptr) {
        for (std::string& s : lastBytes_) s.clear();
        return poll(world, log);
    }

    const std::vector<std::string>& files() const { return files_; }
    int polls() const { return polls_; }
    int reloads() const { return reloads_; }

private:
    std::vector<std::string> files_;
    std::vector<std::string> lastBytes_;
    int polls_ = 0;
    int reloads_ = 0;
};

}  // namespace dlab
