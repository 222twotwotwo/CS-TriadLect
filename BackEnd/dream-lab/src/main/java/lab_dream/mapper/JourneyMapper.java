package lab_dream.mapper;

import lab_dream.entity.Journey;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

/** 访客足迹的仓库。翻页（Pageable）交给 Spring，你只管说"要最新的几条"。 */
public interface JourneyMapper extends JpaRepository<Journey, Long> {

    /** 最新的一页足迹（档案馆从后往前翻书） */
    List<Journey> findAllByOrderByIdDesc(Pageable pageable);

    /** 访客总共发出了多少条请求 —— 通关证书上会用到 */
    long count();

    /** 对某个路径发过多少次请求（守门人靠它决定嘲讽力度） */
    long countByPath(String path);
}
