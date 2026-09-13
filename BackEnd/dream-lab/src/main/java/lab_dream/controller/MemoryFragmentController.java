package lab_dream.controller;

import lab_dream.dto.WhisperRequest;
import lab_dream.entity.MemoryFragment;
import lab_dream.entity.Whisper;
import lab_dream.exception.GuardException;
import lab_dream.mapper.JourneyMapper;
import lab_dream.mapper.WhisperMapper;
import lab_dream.service.GameStateService;
import lab_dream.service.StoryService;
import lab_dream.service.WorldRules;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Map;

/**
 * ════════════════════════════════════════════════════════════
 *  星野凛 的咒语清单（历代管理员批注版）
 *
 *  在这个世界里，一条「路由」就是一句咒语：
 *  对着服务器念对了，它就会回应你。
 *
 *  GET  /rin/hello                      星野凛 的耳朵（初次问候）
 *  GET  /memory/fragment/2               ??? —— 还没有人召唤过它，要不要试试？
 *  GET  /memory/fragment/3?spell=???     守门人把守（咒语失传，线索在 星野凛 的老歌里）
 *  GET  /archive/journey                 档案馆 · 访客之书（记录着你做过的一切）
 *  POST /monitor/verdict                 监控中心 · 锁定真相（风暴之夜的秘密）
 *  GET  /memory/fragment/6               ??? —— 似乎与世界规则有关
 *  POST /memory/fragment/7               ??? —— 要用「写」的方式开启
 *
 *  前面带 GET 的：去「取」一样东西；带 POST 的：往里「放」一样东西。
 * ════════════════════════════════════════════════════════════
 */
@RestController
public class MemoryFragmentController {

    private final GameStateService gameState;
    private final JourneyMapper journeys;
    private final StoryService story;
    private final WorldRules world;
    private final WhisperMapper whispers;

    public MemoryFragmentController(GameStateService gameState, JourneyMapper journeys,
                                    StoryService story, WorldRules world,
                                    WhisperMapper whispers) {
        this.gameState = gameState;
        this.journeys = journeys;
        this.story = story;
        this.world = world;
        this.whispers = whispers;
    }

    /**
     * GET /memory/fragment/2
     * 你找到了！这个路由是历代管理员写在注释里的"密码本"。
     * 对，就是这么朴素：所谓 API 文档，很多时候就是代码顶上这些注释。
     */
    @GetMapping("/memory/fragment/2")
    public Map<String, Object> fragment2() {
        gameState.requireUnlocked(2);
        MemoryFragment fragment = gameState.fragment(2);
        if (fragment.isFound()) {
            return Map.of("message", "这片碎片已经在你手里了，不用念第二遍。", "fragmentCollected", false);
        }
        return Map.of(
                "message", story.text("/fragments/2/doneLine"),
                "fragmentCollected", true,
                "fragment", Map.of("id", fragment.getId(), "title", fragment.getTitle()),
                "foundLine", gameState.collect(fragment));
    }

    /**
     * GET /memory/fragment/3?spell=?
     * 守门人把守的碎片。spell 参数不对，就吃一发嘲讽。
     * —— 参数校验：后端最日常的 Defensive 动作，被我们做成了游戏。
     */
    @GetMapping("/memory/fragment/3")
    public Map<String, Object> fragment3(@RequestParam(required = false) String spell) {
        gameState.requireUnlocked(3);

        if (spellOk(spell)) {
            MemoryFragment fragment = gameState.fragment(3);
            if (fragment.isFound()) {
                return Map.of("message", "守门人已经放行过你了。碎片在你手里。", "fragmentCollected", false);
            }
            return Map.of(
                    "message", story.text("/fragments/3/doneLine"),
                    "fragmentCollected", true,
                    "fragment", Map.of("id", fragment.getId(), "title", fragment.getTitle()),
                    "foundLine", gameState.collect(fragment));
        }

        // 嘲讽会升级：档案馆记着你试了几次，我们按次数念不同的台词
        int attempts = (int) journeys.countByPath("/memory/fragment/3");
        var taunts = story.node("/fragments/3/guardTaunts");
        String taunt = taunts.get(attempts % taunts.size()).asText();
        throw new GuardException(spell == null ? "你空着手来了——咒语呢？（提示：请求后面要带上 ?spell=…）\n" + taunt : taunt);
    }

    /**
     * GET /memory/fragment/6 —— 热修判定。
     * 玩家改完 config/world.properties 并重启后，这里的读数才会变成 100。
     */
    @GetMapping("/memory/fragment/6")
    public Map<String, Object> fragment6() {
        gameState.requireUnlocked(6);
        MemoryFragment fragment = gameState.fragment(6);

        if (!world.isStable()) {
            // 还没修好：不报错，把读数和提示一起递回去（游戏里，失败也是信息）
            return Map.of(
                    "message", story.text("/fragments/6/notYetLine"),
                    "stability", world.stability(),
                    "configFile", WorldRules.CONFIG_FILE.toString(),
                    "hint", story.text("/fragments/6/hint"),
                    "fragmentCollected", false);
        }
        if (fragment.isFound()) {
            return Map.of("message", "世界已经稳稳的了。这片碎片早就在你手里。", "fragmentCollected", false);
        }
        return Map.of(
                "message", story.text("/fragments/6/doneLine"),
                "fragmentCollected", true,
                "fragment", Map.of("id", fragment.getId(), "title", fragment.getTitle()),
                "foundLine", gameState.collect(fragment),
                "nextHint", story.text("/fragments/7/taskLine"));
    }

    /**
     * POST /memory/fragment/7 —— 最后一片碎片不是找回来的，是"写"进去的。
     * 你的话会存进 whisper 表，重启也不会消失。这就是「持久化」。
     */
    @PostMapping("/memory/fragment/7")
    public Map<String, Object> fragment7(@RequestBody WhisperRequest request) {
        gameState.requireUnlocked(7);
        if (request == null || request.message() == null || request.message().isBlank()) {
            throw new IllegalArgumentException("总得说点什么吧——请求体里写上 {\"message\": \"…\"}");
        }
        String message = request.message().trim();
        int n = message.replace("\r", "").replace("\n", "").length();
        if (n > 200) {
            throw new IllegalArgumentException("星野凛 只记得住 200 字以内的话（当前 " + n + " 字）");
        }

        MemoryFragment fragment = gameState.fragment(7);
        if (fragment.isFound()) {
            return Map.of("message", "你的话已经写在她的世界里了。", "fragmentCollected", false);
        }
        whispers.save(new Whisper(message));
        return Map.of(
                "message", story.text("/fragments/7/doneLine"),
                "fragmentCollected", true,
                "fragment", Map.of("id", fragment.getId(), "title", fragment.getTitle()),
                "foundLine", gameState.collect(fragment),
                "nextHint", story.text("/finale/certLine"));
    }

    private static boolean spellOk(String spell) {
        if (spell == null) return false;
        String expect = new String(Base64.getDecoder().decode("bHVtb3M="), StandardCharsets.UTF_8);
        return spell.trim().equalsIgnoreCase(expect);
    }
}
