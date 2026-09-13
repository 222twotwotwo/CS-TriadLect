package lab_dream.controller;

import lab_dream.service.GameStateService;
import lab_dream.service.StoryService;
import lab_dream.service.WorldRules;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.Map;

/**
 * 世界状态 —— 第 6 关的"仪表读数"。
 *
 * 前端的崩坏特效（画面抖动、色差、雪花）全看这里的 stability 数值。
 */
@RestController
public class WorldController {

    private final WorldRules world;
    private final GameStateService gameState;
    private final StoryService story;

    public WorldController(WorldRules world, GameStateService gameState, StoryService story) {
        this.world = world;
        this.gameState = gameState;
        this.story = story;
    }

    @GetMapping("/world/status")
    public Map<String, Object> status() {
        Map<String, Object> out = new HashMap<>();
        out.put("stability", world.stability());
        out.put("label", story.text("/world/stabilityLabel"));
        out.put("configFile", WorldRules.CONFIG_FILE.toString());
        // 稳定度没修好时，把第 6 关的任务提示一并送上，玩家不用来回翻
        if (!world.isStable()) {
            out.put("hint", story.text("/fragments/6/taskLine"));
        }
        return out;
    }
}
