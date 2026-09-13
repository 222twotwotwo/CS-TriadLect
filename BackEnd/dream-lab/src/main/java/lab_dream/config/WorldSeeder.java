package lab_dream.config;

import lab_dream.entity.MemoryFragment;
import lab_dream.mapper.MemoryFragmentMapper;
import lab_dream.service.StoryService;
import lab_dream.service.WorldRules;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

/**
 * 开服仪式。每次启动做两件事：
 *
 *  1. 检查七片碎片是否都"登记在册"，缺哪片补哪片（found=false，等着被找回）。
 *  2. 检查世界规则：如果配置文件里的稳定度已经被改成 100，而玩家正卡在第 6 关，
 *     就替他把这一步走完 —— 因为"改配置 + 重启"这两件真正要学的事，他已经做完了。
 *     页面那边一刷新就会自动接上，不用再回游戏里点一次"我改好了"。
 *
 * CommandLineRunner = "等 Spring Boot 完全启动后，帮我跑这个方法"。
 */
@Component
public class WorldSeeder implements CommandLineRunner {

    private static final Logger log = LoggerFactory.getLogger(WorldSeeder.class);

    private final MemoryFragmentMapper fragments;
    private final StoryService story;
    private final WorldRules world;

    public WorldSeeder(MemoryFragmentMapper fragments, StoryService story, WorldRules world) {
        this.fragments = fragments;
        this.story = story;
        this.world = world;
    }

    @Override
    public void run(String... args) {
        seedFragments();
        resumeHotfixIfRepaired();
    }

    /** 把还没登记过的碎片补上（不覆盖已找回的） */
    private void seedFragments() {
        for (int i = 1; i <= 7; i++) {
            if (fragments.existsById(i)) {
                continue;
            }
            fragments.save(new MemoryFragment(i, story.text("/fragments/" + i + "/title")));
        }
    }

    /**
     * 第 6 关的"自动续上"：
     * 世界已经是完好的（stability >= 100），前面五片都在手里，第六片还没到手 ——
     * 说明玩家刚刚完成了"改配置 + 重启"，那就直接把第六片交给他。
     * 反过来，如果世界还是坏的，或者他还没走到第 6 关，就什么都不做。
     */
    private void resumeHotfixIfRepaired() {
        if (!world.isStable()) {
            return;
        }
        if (!fragments.existsByIdAndFoundTrue(5) || fragments.existsByIdAndFoundTrue(6)) {
            return;
        }
        fragments.findById(6).ifPresent(f -> {
            f.collect();
            fragments.save(f);
            log.info("世界稳定度 {} —— 配置已被修好，第 6 片碎片自动归位（回到网页即可继续）",
                    world.stability());
        });
    }
}
