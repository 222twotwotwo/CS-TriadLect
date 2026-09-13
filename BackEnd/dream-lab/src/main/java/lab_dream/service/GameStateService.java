package lab_dream.service;

import lab_dream.dto.FragmentView;
import lab_dream.dto.GameStateResponse;
import lab_dream.entity.MemoryFragment;
import lab_dream.entity.Whisper;
import lab_dream.exception.StoryException;
import lab_dream.mapper.JourneyMapper;
import lab_dream.mapper.MemoryFragmentMapper;
import lab_dream.mapper.WhisperMapper;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * 世界的"进度条"：碎片找回了多少、下一片在哪、访客是谁。
 * 每个关卡动手前都会来问它一句"现在轮到第几片了"。
 */
@Service
public class GameStateService {

    private final MemoryFragmentMapper fragments;
    private final JourneyMapper journeys;
    private final WhisperMapper whispers;
    private final StoryService story;

    public GameStateService(MemoryFragmentMapper fragments, JourneyMapper journeys,
                            WhisperMapper whispers, StoryService story) {
        this.fragments = fragments;
        this.journeys = journeys;
        this.whispers = whispers;
        this.story = story;
    }

    /** 回答"这个世界现在怎么样了" */
    public GameStateResponse state() {
        int found = (int) fragments.countByFoundTrue();
        Integer next = nextFragmentId();
        String nextTitle = next == null ? null : fragments.findById(next).map(MemoryFragment::getTitle).orElse(null);
        String lastWhisper = whispers.findTopByOrderByCreatedAtDesc().map(Whisper::getMessage).orElse(null);
        boolean returning = whispers.count() > 0; // 库里有悄悄话 = 你来过（二周目 星野凛 会认出你）
        return new GameStateResponse(
                found, 7, next, nextTitle,
                fragments.findByFoundTrueOrderByFoundAtAsc().stream()
                        .map(f -> new FragmentView(f.getId(), f.getTitle())).toList(),
                journeys.count(), returning, lastWhisper);
    }

    /** 取某片碎片；不存在就报 404（剧情数据的编号写错了也会在这里暴露） */
    public MemoryFragment fragment(int id) {
        return fragments.findById(id)
                .orElseThrow(() -> new StoryException("编号 " + id + " 的碎片不属于这个世界（1~7）"));
    }

    /** 碎片要按顺序找回：第 n 片只在第 n-1 片到手后才会显形 */
    public void requireUnlocked(int id) {
        if (id > 1 && !fragments.existsByIdAndFoundTrue(id - 1)) {
            throw new StoryException(
                    "第 " + id + " 片碎片还隐在雾里。星野凛：「别急，先找第 " + (id - 1) + " 片。」");
        }
    }

    /** 碎片到手！写库 + 返回 星野凛 的反应 */
    public String collect(MemoryFragment fragment) {
        fragment.collect();
        fragments.save(fragment);
        return story.text("/rin/foundLines/" + fragment.getId());
    }

    /** 第一片还没被找回的碎片编号；全找完了返回 null */
    public Integer nextFragmentId() {
        for (int i = 1; i <= 7; i++) {
            if (!fragments.existsByIdAndFoundTrue(i)) {
                return i;
            }
        }
        return null;
    }

    /**
     * "再来一次"：把进度清空，七片碎片重新散落，并把世界规则写回"崩塌"状态。
     * 数据库这边立刻生效；配置文件那边要重启服务器才生效（第 6 关教过这件事）。
     */
    public void restart() {
        journeys.deleteAll();
        whispers.deleteAll();
        fragments.deleteAll();
        for (int i = 1; i <= 7; i++) {
            fragments.save(new MemoryFragment(i, story.text("/fragments/" + i + "/title")));
        }
        WorldRules.writeStability(0);
    }

    /** ---- 对外形状（Java 21 的 record：一行顶一个不可变类）---- */
}
